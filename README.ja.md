# certbot-to-asa

Let’s Encrypt などで取得した **公開 TLS 証明書を Cisco ASA へ自動反映** するための、Certbot *deploy hook* 連携スクリプトです。
`outside` インターフェースで公開 Web VPN ポータルや AnyConnect 接続を提供している環境向けに設計されています。

> **注意**: 公開 TLS 証明書の最長有効期間は今後さらに短縮される見込みです（現時点では最大 **47 日**）。
> 証明書の有効期間短縮は CA/B Forum Ballot によって変動するため、運用前に一次情報をご確認ください。
<img width="1229" height="625" alt="Screenshot 2025-10-28 at 15 14 32" src="https://github.com/user-attachments/assets/c511ff2f-6b89-439a-838f-92720b5ec743" />

---

### ⚠️ 証明書有効期間短縮について

CA/Browser Forum により、公開 TLS 証明書の最長有効期間は段階的に短縮され、最終的には **47 日** となる予定です（Ballot **SC-081 v3**, 2025 年 4 月 11 日発表）。

* **出典:** [CA/Browser Forum Ballot SC-081 v3 (2025-04-11)](https://cabforum.org/2025/04/11/ballot-sc081v3-introduce-schedule-of-reducing-validity-and-data-reuse-periods/)
* **概要:** 398 日 → 200 日 → 100 日 → **47 日** へ段階的短縮（最終段階は 2029 年頃）
* **参考:** [DigiCert ブログ — *TLS Certificate Lifetimes Will Officially Reduce to 47 Days*](https://www.digicert.com/blog/tls-certificate-lifetimes-will-officially-reduce-to-47-days)

この短縮は、秘密鍵漏えいリスクの低減と証明書運用の俊敏化を目的としており、
今後は **自動更新フック（deploy hook）による自動反映** が実運用上の前提となります。

---

## 特長

* Certbot の **deploy hook** で証明書更新直後に自動適用
* ASA へ SSH 経由でアクセスし、**新しい証明書を PKCS#12(Base64)** でインポート
* 既存 trustpoint の **Serial 異同を検出して自動更新**
* 更新後、自動的に `ssl trust-point ... outside` を再設定し **write memory**
* 設定済み trustpoint が同一 Serial の場合は **安全にスキップ**
* ログを `/var/log/letsencrypt/asa.log` に記録

---

## 動作要件

* Cisco ASA（9.12 以降推奨、PKCS#12 インポート対応）
* Certbot（`renewal-hooks/deploy` ディレクトリが利用可能であること）
* Linux（Debian / Ubuntu など）
* `python3`, `pexpect`, `openssl`

---

## 仕組み（概要）

1. Certbot が証明書を更新
2. deploy hook（本スクリプト）が起動
3. 新旧証明書の **Serial 異同を確認**
4. 新しい証明書 + 鍵を **PKCS#12 + Base64** に変換
5. SSH で ASA にログインし、既存 trustpoint の Serial を取得
6. 異なれば：

   * 新しい trustpoint を作成 (`LE-Portal-YYYYMMDDHHMMSS`)
   * Base64 を `crypto ca import ... pkcs12` 経由で投入
   * `ssl trust-point` を再設定し `write memory` を実行
   * 古い trustpoint と chain を削除
7. 同一 Serial の場合はスキップして終了

---

## ディレクトリ構成（推奨）

```text
/etc/letsencrypt/
  renewal-hooks/
    deploy/
      certbot-to-asa.py                 # ← このスクリプト（root:root 0750）
  credentials/
    asa.pass                            # ← LOGIN/ENABLE パスワード（root:root 0600）
  hooks.d/
    asa.env                             # ← 非機密設定（root:root 0640）
/var/log/letsencrypt/
  asa.log                               # ← ログ（root:adm 0640）
```

> **メモ:**
> スクリプト内では `ASA_ENV_FILE` 環境変数で env ファイルの場所を上書き可能です。
> 例：`ASA_ENV_FILE=/etc/letsencrypt/hooks.d/asa.env`

---

## 設定

### 1) ASA パスワードファイル（`asa.pass`）

```bash
# /etc/letsencrypt/credentials/asa.pass（600）
LOGIN=YourLoginPassword
ENABLE=YourEnablePassword
```

---

### 2) 環境ファイル（`asa.env`）

```bash
# /etc/letsencrypt/hooks.d/asa.env（640）

ASA_HOST=asa.example.com
ASA_USER=admin
ASA_PASSWORD_FILE=/etc/letsencrypt/credentials/asa.pass

# 証明書関連
ASA_DOMAIN=example.com
ASA_PKCS12_PASS=exportpass
```

> **ヒント:**
> Certbot から渡される環境変数 `RENEWED_LINEAGE` を自動で利用します。
> 手動テスト時は明示的に指定する必要があります。

---

## 使い方

### A. Certbot の deploy hook として自動実行

`certbot renew` 実行時に、自動で起動されます。特別な設定は不要です。

---

### B. 手動テスト（動作確認）

```bash
sudo -E RENEWED_LINEAGE=/etc/letsencrypt/live/example.com \
  python3 -u /etc/letsencrypt/renewal-hooks/deploy/certbot-to-asa.py
```

> **ポイント:**
> 同一 Serial の場合は `[INFO] Certificate is up-to-date.` と表示され、ASA への書き込みは行われません。

---

## 既定挙動（安全設計）

* 証明書の Serial が一致すれば **スキップ**
* Serial が異なれば、新 trustpoint を作成 → `ssl trust-point` を再設定
* 古い trustpoint は自動削除
* PKCS#12 は一時ファイルとして生成し、インポート後に自動削除

---

## トラブルシュート

### 1) env が読まれていない

```bash
sudo -E RENEWED_LINEAGE=/etc/letsencrypt/live/example.com \
  python3 -u /etc/letsencrypt/renewal-hooks/deploy/certbot-to-asa.py
```

出力に `[WARN] Env file not found:` が出る場合は、`ASA_ENV_FILE` を明示指定してください。

---

### 2) SSH ログイン失敗

```
[ERR] SSH login failed: Permission denied.
```

* `asa.pass` 内の `LOGIN` パスワードが正しいか確認
* `ASA_USER` の privilege level が 15 であること
* 公開鍵認証を使っている場合は、`pexpect` の挙動を変更する必要があります（パスワード前提）

---

### 3) 証明書更新されない

* 旧 trustpoint の Serial と一致している → 正常（更新不要）
* `openssl x509 -noout -serial` で local serial を確認

---

### 4) Import 途中でタイムアウト

* ASA 側のコンソール出力やログを確認 (`show logging`)
* 大きな証明書チェーン（>4KB）ではタイムアウトを延ばす（`pexpect.timeout` 調整）

---

## セキュリティ

* `/etc/letsencrypt/credentials/asa.pass` は **root:root 0600**
* `.env` ファイルにはパスワードを含めない
* PKCS#12 のパスワード (`ASA_PKCS12_PASS`) は安全な文字列を使用
* ログには秘密情報を出力しない

---

## 既知の落とし穴

* ASA のバージョンが古く `crypto ca import ... pkcs12` が動作しない
* CLI 出力に色制御文字が混ざる場合、`pexpect` のマッチングに失敗することあり
* 証明書 import 中にコンソールで別のセッションが `crypto ca` を実行すると競合

---

## ライセンス

License: **0BSD**
本リポジトリに関して著作者人格権を行使しません。

---

## Author

Hideaki Shimomura 


