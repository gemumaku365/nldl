# NLDL - Narou Light Novel Downloader

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**小説家になろう** の小説をダウンロードするデスクトップアプリケーションです。

## 機能

- **作品ID / URL 指定**: 作品ID（`n9669bk`）または URL（`https://ncode.syosetu.com/n9669bk/`）のどちらでも入力可能
- **並列ダウンロード**: 最大100の並列接続で高速ダウンロード
- **2つの保存形式**:
  - 「まとめる」— 全話を1つのテキストファイルに
  - 「1話ずつ分ける」— 各話を個別のテキストファイルに
- **プログレス表示**: ダウンロード進捗をプログレスバーとログで表示
- **サーバーに優しい設計**: User-Agentローテーション、レート制限対応、指数バックオフリトライ

## 必要なもの

- **Windows**, **macOS**, **Linux**
- **Python 3.10 以上**
- 依存パッケージ（自動インストール可）

## インストールと実行

### 1. リポジトリをクローン

```bash
git clone https://github.com/gemumaku/nldl.git
cd nldl
```

### 2. 依存パッケージをインストール

```bash
pip install -r requirements.txt
```

### 3. 起動

```bash
python main.py
```

> **初回起動時に `ModuleNotFoundError` が出た場合** → `pip install -r requirements.txt` を実行してください。

## 使い方

1. 作品ID（`n9669bk`）または URL を入力
2. 保存先フォルダを選択
3. 並列取得数と遅延を必要に応じて調整
4. 「ダウンロード開始」をクリック
5. 完了するとテキストファイルが保存されます

## 出力ファイル

### 「まとめる」モード
```
{タイトル}.txt  — Shift-JIS エンコード
```

### 「1話ずつ分ける」モード
```
{タイトル}/
  ├── 0001_{サブタイトル}.txt
  ├── 0002_{サブタイトル}.txt
  └── ...
```

## 注意事項

- **個人利用の範囲内でご使用ください**
- サーバーに過剰な負荷をかけないよう、並列数と遅延は適切に設定してください
- 作品的利用（二次創作など）の権利は各作者様に帰属します
- ダウンロードした作品の取り扱いには十分ご注意ください

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) を参照してください。
