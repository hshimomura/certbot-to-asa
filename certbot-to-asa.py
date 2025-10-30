#!/usr/bin/env python3
import subprocess, base64, tempfile, datetime, os, sys, re, pexpect, logging
from pathlib import Path

# === 設定 ===
def load_env_file(path):
    """KEY=VALUE 形式の .env ファイルを読み込んで os.environ に追加"""
    if not Path(path).exists():
        print(f"[WARN] Env file not found: {path}")
        return
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

# === .env 読み込み ===
ENV_PATH = os.environ.get("ASA_ENV_FILE", "/etc/letsencrypt/hooks.d/asa.env")
load_env_file(ENV_PATH)

# .env内の値を優先して環境変数から読む
ASA_HOST = os.environ.get("ASA_HOST", "asa")
ASA_USER = os.environ.get("ASA_USER", "asauser")
ASA_PASS_LOGIN = os.environ.get("ASA_PASS_LOGIN", "asapassword")
ASA_PASS_ENABLE = os.environ.get("ASA_PASS_ENABLE", ASA_PASS_LOGIN)
DOMAIN = os.environ.get("ASA_DOMAIN", "example.com")

LE_PATH = os.environ.get("RENEWED_LINEAGE", f"/etc/letsencrypt/live/{DOMAIN}")
FULLCHAIN = os.path.join(LE_PATH, "fullchain.pem")
PRIVKEY = os.path.join(LE_PATH, "privkey.pem")
PKCS12_PASS = os.environ.get("ASA_PKCS12_PASS", "password")

def load_password_file(path):
    """LOGIN=... ENABLE=... の形式で定義されたパスワードファイルを読む"""
    creds = {}
    if not Path(path).exists():
        print(f"[WARN] Password file not found: {path}")
        return creds
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                creds[key.strip().upper()] = val.strip()
    return creds

creds = load_password_file(os.environ.get("ASA_PASSWORD_FILE", ""))
ASA_PASS_LOGIN = creds.get("LOGIN", os.environ.get("ASA_PASS_LOGIN", ""))
ASA_PASS_ENABLE = creds.get("ENABLE", os.environ.get("ASA_PASS_ENABLE", ""))


logging.basicConfig(
    filename="/var/log/letsencrypt/asa.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


# === Fingerprint / Serial 取得 ===
def get_local_serial():
    out = subprocess.check_output(
        ["openssl", "x509", "-in", FULLCHAIN, "-noout", "-serial"],
        encoding="utf-8"
    ).strip()
    m = re.search(r"serial=([0-9A-Fa-f]+)", out)
    if not m:
        print("[ERR] Cannot parse serial from fullchain.pem")
        sys.exit(1)
    serial = m.group(1).upper()
    print(f"[INFO] Local serial: {serial}")
    return serial

# === ASA 接続 ===
def connect_asa():
    child = pexpect.spawn(
        f"ssh -o StrictHostKeyChecking=accept-new {ASA_USER}@{ASA_HOST}",
        encoding="utf-8", timeout=30
    )
    child.expect("[Pp]assword:")
    child.sendline(ASA_PASS_LOGIN)
    i = child.expect([">", "#", "Permission denied", pexpect.TIMEOUT], timeout=30)
    if i == 2:
        print("[ERR] SSH login failed: Permission denied.")
        sys.exit(1)
    elif i == 3:
        print("[ERR] SSH login timeout.")
        sys.exit(1)
    print("[INFO] Logged in successfully.")

    # enableモードへ昇格
    if i == 0:
        child.sendline("enable")
        child.expect("Password:")
        child.sendline(ASA_PASS_ENABLE)
        child.expect("#", timeout=10)
        print("[INFO] Entered enable mode successfully.")
    else:
        print("[INFO] Already in enable mode.")
    return child

# === 現在の trustpoint 確認 ===
def get_current_trustpoint(child):
    child.sendline("show run | include ssl trust-point")
    child.expect("#", timeout=10)
    out = child.before
    m = re.search(r"ssl trust-point\s+(\S+)\s+outside", out)
    tp = m.group(1) if m else None
    print(f"[INFO] Current trustpoint: {tp or '(none)'}")
    return tp

# === ASA 側の証明書シリアルを取得 ===
def get_asa_serial(child, tp_name):
    if not tp_name:
        return None
    child.sendline(f"show crypto ca certificates {tp_name}")
    child.expect("#", timeout=15)
    out = child.before
    m = re.search(r"Certificate Serial Number:\s*([0-9A-Fa-f]+)", out)
    if not m:
        print(f"[WARN] No serial found for {tp_name}")
        return None
    serial = m.group(1).upper()
    print(f"[INFO] ASA serial for {tp_name}: {serial}")
    return serial

# === PKCS#12 生成 (Base64) ===
def generate_p12_b64():
    tmp = tempfile.NamedTemporaryFile(delete=True, suffix=".p12")
    cmd = [
        "openssl", "pkcs12", "-export",
        "-in", FULLCHAIN, "-inkey", PRIVKEY,
        "-out", tmp.name, "-passout", f"pass:{PKCS12_PASS}"
    ]
    subprocess.run(cmd, check=True)
    with open(tmp.name, "rb") as f:
        b64 = base64.encodebytes(f.read()).decode("ascii")
    os.unlink(tmp.name)
    print(f"[INFO] Generated PKCS#12 and Base64 encoded ({len(b64)} bytes)")
    return b64

# === trustpoint 削除 ===
def delete_trustpoint(child, tp_name):
    if not tp_name:
        return
    print(f"[INFO] Removing old trustpoint {tp_name} ...")
    child.sendline("conf t")
    child.expect(r"\(config.*#", timeout=10)

    # ① IKEv2 remote-access trustpoint を解除
    child.sendline(f"no crypto ikev2 remote-access trustpoint {tp_name}")
    child.expect("#", timeout=10)

    # ② trustpoint を削除
    child.sendline(f"no crypto ca trustpoint {tp_name}")
    # プロンプトに改行や色が混じる場合を考慮し、柔軟にマッチング
    i = child.expect([r"Are you sure.*yes/no.*", "#"], timeout=15)
    if i == 0:
        print("[DEBUG] Confirmation prompt detected → sending 'yes'")
        child.sendline("yes")
        # confirm のあとに複数行メッセージが出るので少し長めに待つ
        child.expect("#", timeout=20)
    else:
        print("[DEBUG] No confirmation prompt, already removed?")

    # ③ 証明書 chain を削除（trustpoint 削除後でOK）
    child.sendline(f"no crypto ca certificate chain {tp_name}")
    child.expect("#", timeout=10)

    child.sendline("end")
    child.expect("#")
    print(f"[INFO] Trustpoint {tp_name} and its certificate chain removed.")

# === 新しい trustpoint 登録 ===
def import_new_cert(child, b64data):
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    tp_name = f"LE-Portal-{ts}"
    print(f"[INFO] Creating new trustpoint {tp_name}")

    child.sendline("conf t")
    child.expect(r"\(config.*#", timeout=10)
    child.sendline(f"crypto ca trustpoint {tp_name}")
    child.expect(r"\(config-ca-trustpoint\)#", timeout=10)
    child.sendline("enrollment terminal")
    child.sendline(f"fqdn {DOMAIN}")
    child.sendline(f"keypair {tp_name}-key")
    child.sendline("exit")
    child.sendline(f"crypto ca import {tp_name} pkcs12 {PKCS12_PASS}")
    child.expect("base 64 encoded pkcs12", timeout=10)
    child.sendline(b64data)
    child.sendline("quit")
    child.expect("Import PKCS12 operation completed successfully", timeout=120)
    print(f"[INFO] {tp_name} import successful.")
    child.sendline("ssl trust-point " + tp_name + " outside")
    child.sendline("end")
    child.expect("#")
    child.sendline("write memory")
    child.expect(["OK", "#"], timeout=90)
    print(f"[OK] ASA trustpoint updated to {tp_name}")
    return tp_name

# === メイン処理 ===
def main():
    local_serial = get_local_serial()
    child = connect_asa()
    current_tp = get_current_trustpoint(child)
    asa_serial = get_asa_serial(child, current_tp)
    if asa_serial == local_serial:
        print("[INFO] Certificate is up-to-date. No action needed.")
        sys.exit(0)

    print(f"[INFO] Certificate differs (ASA={asa_serial} / Local={local_serial})")
    b64data = generate_p12_b64()
    new_tp = import_new_cert(child, b64data)

    if current_tp:
        delete_trustpoint(child, current_tp)

    print("[DONE] ASA certificate updated successfully.")

if __name__ == "__main__":
    main()
