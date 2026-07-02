import sys
import re
import time
import random
import threading
import requests
import os

from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QFileDialog,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QProgressBar,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QAbstractSpinBox,
)

from PySide6.QtCore import Signal, QObject
from PySide6.QtGui import QIcon


# 複数のUser-Agentをローテーション（ダウンロード速度を落とさず分散）
USER_AGENTS = [
    # Chrome系
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    # Edge系
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36 Edg/135.0.0.0",
    # Firefox系
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:137.0) Gecko/20100101 Firefox/137.0",
]

# Accept-Languageのバリエーション
ACCEPT_LANGUAGES = [
    "ja,en;q=0.9,en-US;q=0.8",
    "ja-JP,ja;q=0.9,en;q=0.8",
    "ja,en-US;q=0.9,en;q=0.8",
    "en-US,en;q=0.9,ja;q=0.8",
]


def rotate_headers():
    """リクエストごとにヘッダーをローテーション"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": random.choice(ACCEPT_LANGUAGES),
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }


class DownloaderSignals(QObject):
    log = Signal(str)
    progress = Signal(int)
    finished = Signal(str)
    error = Signal(str)


class NarouDownloader:
    def __init__(
        self,
        ncode,
        save_dir,
        max_workers,
        delay,
        save_mode,
        signals,
    ):
        self.ncode = ncode.lower().strip()
        self.save_dir = save_dir
        self.max_workers = max_workers
        self.delay = delay
        self.save_mode = save_mode
        self.signals = signals

        # セッションは使わず毎回新しいヘッダー + 独立した接続でプロファイルを隠す
        # ただし短時間に大量開設しすぎないよう注意
        self.last_request_time = 0.0

    def _throttle(self):
        """ミニマムインターバルを確保（0.2秒以下の間隔で連続しない）"""
        elapsed = time.time() - self.last_request_time
        if elapsed < 0.25:
            time.sleep(0.25 - elapsed)
        self.last_request_time = time.time()

    def _request_with_retry(self, url, max_retries=3, timeout=30, allow_404=False):
        """リトライ＆制限回避付きリクエスト
        allow_404=True の場合、404はリトライせず None を返す
        """
        retry_delays = [1, 2, 4, 8]  # 指数バックオフ

        for attempt in range(max_retries + 1):
            self._throttle()

            try:
                headers = rotate_headers()
                # クッキーなしの独立したリクエスト（セッション固定されないように）
                r = requests.get(url, headers=headers, timeout=timeout)

                if r.status_code == 404 and allow_404:
                    return None

                if r.status_code == 429:
                    wait = retry_delays[min(attempt, len(retry_delays) - 1)]
                    self.signals.log.emit(
                        f"⚠ 制限検出(429) {wait}秒待機してリトライ"
                    )
                    time.sleep(wait + random.uniform(0.5, 2.0))
                    continue

                if r.status_code == 503:
                    wait = retry_delays[min(attempt, len(retry_delays) - 1)]
                    self.signals.log.emit(
                        f"⚠ 一時的不具合(503) {wait}秒待機してリトライ"
                    )
                    time.sleep(wait + random.uniform(0.5, 2.0))
                    continue

                r.raise_for_status()
                return r

            except (requests.ConnectionError, requests.Timeout) as e:
                wait = retry_delays[min(attempt, len(retry_delays) - 1)]
                self.signals.log.emit(
                    f"⚠ 接続エラー {wait}秒待機してリトライ ({e})"
                )
                time.sleep(wait + random.uniform(0.5, 1.5))
                continue

        raise Exception(f"最大リトライ回数超過: {url}")

    def run(self):
        try:
            title, links = self.get_episode_links()

            self.signals.log.emit(f"タイトル: {title}")
            self.signals.log.emit(f"総話数: {len(links)}")
            self.signals.log.emit(f"並列数: {self.max_workers}")
            self.signals.log.emit(f"遅延: {self.delay}秒")

            total = len(links)
            completed = 0

            results = [None] * total

            with ThreadPoolExecutor(
                max_workers=self.max_workers
            ) as executor:

                futures = {
                    executor.submit(
                        self.get_text,
                        index,
                        url
                    ): index
                    for index, url in enumerate(links)
                }

                for future in as_completed(futures):
                    index = futures[future]

                    try:
                        result = future.result()

                        if result:
                            results[index] = result

                    except Exception as e:
                        self.signals.log.emit(
                            f"取得エラー: {e}"
                        )

                    completed += 1

                    progress = int(
                        (completed / total) * 100
                    )

                    self.signals.progress.emit(progress)

                    self.signals.log.emit(
                        f"[{completed}/{total}] 完了"
                    )

                    if self.delay > 0:
                        time.sleep(self.delay)

            contents = [
                r for r in results
                if r
            ]

            if self.save_mode == "まとめる":
                path = self.save_single_txt(
                    title,
                    contents
                )

            else:
                path = self.save_split_txts(
                    title,
                    contents
                )

            self.signals.finished.emit(path)

        except Exception as e:
            self.signals.error.emit(str(e))

    def get_episode_links(self):
        base_url = (
            f"https://ncode.syosetu.com/"
            f"{self.ncode}/"
        )

        links = []
        title = None

        page = 1

        while True:
            if page == 1:
                url = base_url
            else:
                url = f"{base_url}?p={page}"

            self.signals.log.emit(
                f"一覧取得: {url}"
            )

            r = self._request_with_retry(url, allow_404=True)

            if r is None:
                self.signals.log.emit(
                    f"最終ページ到達（404）"
                )
                break

            self.signals.log.emit(
                f"HTTP {r.status_code}"
            )

            soup = BeautifulSoup(
                r.text,
                "html.parser"
            )

            if not title:
                title_selectors = [
                    ".novel_title",
                    ".p-novel__title",
                    "h1",
                ]

                for selector in title_selectors:
                    elem = soup.select_one(
                        selector
                    )

                    if elem:
                        title = elem.get_text(
                            strip=True
                        )
                        break

                if not title:
                    title = self.ncode

            found = 0

            for a in soup.find_all(
                "a",
                href=True
            ):
                href = a["href"]

                if re.match(
                    rf"^/{self.ncode}/\d+/?$",
                    href
                ):
                    full_url = (
                        "https://ncode.syosetu.com"
                        + href
                    )

                    if full_url not in links:
                        links.append(full_url)
                        found += 1

            self.signals.log.emit(
                f"{found}話検出"
            )

            if found == 0:
                break

            page += 1

        if not links:
            self.signals.log.emit(
                "短編として処理"
            )

            links.append(base_url)

        return title, links

    def get_text(self, index, url):
        try:
            r = self._request_with_retry(url)

            soup = BeautifulSoup(
                r.text,
                "html.parser"
            )

            subtitle = f"{index+1}話"

            subtitle_selectors = [
                ".novel_subtitle",
                ".p-novel__title",
                "h1",
            ]

            for selector in subtitle_selectors:
                elem = soup.select_one(
                    selector
                )

                if elem:
                    subtitle = elem.get_text(
                        strip=True
                    )
                    break

            honbun = None

            body_selectors = [
                "#novel_honbun",
                ".p-novel__body",
                ".js-novel-text",
            ]

            for selector in body_selectors:
                honbun = soup.select_one(
                    selector
                )

                if honbun:
                    break

            if not honbun:
                return None

            text = honbun.get_text("\n")

            text = text.replace(
                "\r\n",
                "\n"
            )

            text = text.replace(
                "\r",
                "\n"
            )

            text = text.replace(
                "\n",
                "\r\n"
            )

            return (
                index + 1,
                subtitle,
                text
            )

        except Exception as e:
            self.signals.log.emit(
                f"取得失敗: {url}"
            )

            self.signals.log.emit(str(e))

            return None

    def save_single_txt(
        self,
        title,
        contents
    ):
        safe_title = re.sub(
            r'[\\/:*?"<>|]',
            "_",
            title
        )

        path = (
            f"{self.save_dir}/"
            f"{safe_title}.txt"
        )

        with open(
            path,
            "w",
            encoding="shift_jis",
            errors="replace"
        ) as f:

            f.write(title)
            f.write("\r\n")

            f.write("=" * 60)

            f.write("\r\n\r\n")

            for number, subtitle, text in contents:

                f.write(f"{number}話")
                f.write("\r\n")

                f.write(subtitle)
                f.write("\r\n")

                f.write("-" * 40)
                f.write("\r\n")

                f.write(text)

                f.write("\r\n\r\n")

        return path

    def save_split_txts(
        self,
        title,
        contents
    ):
        safe_title = re.sub(
            r'[\\/:*?"<>|]',
            "_",
            title
        )

        folder = (
            f"{self.save_dir}/"
            f"{safe_title}"
        )

        os.makedirs(
            folder,
            exist_ok=True
        )

        for number, subtitle, text in contents:

            safe_subtitle = re.sub(
                r'[\\/:*?"<>|]',
                "_",
                subtitle
            )

            filename = (
                f"{number:04d}_"
                f"{safe_subtitle}.txt"
            )

            path = (
                f"{folder}/"
                f"{filename}"
            )

            with open(
                path,
                "w",
                encoding="shift_jis",
                errors="replace"
            ) as f:

                f.write(subtitle)
                f.write("\r\n")

                f.write("=" * 40)
                f.write("\r\n\r\n")

                f.write(text)

        return folder


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "NLDL"
        )

        self.resize(900, 700)

        self.setWindowIcon(
            QIcon("icon.ico")
        )

        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        layout.addWidget(
            QLabel("作品ID / URL")
        )

        self.ncode_input = QLineEdit()

        self.ncode_input.setPlaceholderText(
            "例: n9669bk または https://ncode.syosetu.com/n9669bk/"
        )

        layout.addWidget(
            self.ncode_input
        )

        # 保存先
        folder_layout = QHBoxLayout()

        self.folder_input = QLineEdit()

        self.folder_btn = QPushButton(
            "保存先選択"
        )

        self.folder_btn.clicked.connect(
            self.select_folder
        )

        folder_layout.addWidget(
            self.folder_input
        )

        folder_layout.addWidget(
            self.folder_btn
        )

        layout.addLayout(
            folder_layout
        )

        # 並列数
        layout.addWidget(
            QLabel("並列取得数")
        )

        self.worker_spin = QSpinBox()

        self.worker_spin.setRange(
            1,
            100
        )

        self.worker_spin.setValue(10)

        self.worker_spin.setButtonSymbols(
            QAbstractSpinBox.NoButtons
        )

        layout.addWidget(
            self.worker_spin
        )

        # 遅延
        layout.addWidget(
            QLabel("遅延 (秒)")
        )

        self.delay_spin = QDoubleSpinBox()

        self.delay_spin.setRange(
            0,
            10
        )

        self.delay_spin.setSingleStep(
            0.1
        )

        self.delay_spin.setValue(0)

        self.delay_spin.setButtonSymbols(
            QAbstractSpinBox.NoButtons
        )

        layout.addWidget(
            self.delay_spin
        )

        # 保存形式
        layout.addWidget(
            QLabel("保存形式")
        )

        self.save_mode_combo = QComboBox()

        self.save_mode_combo.addItems([
            "まとめる",
            "1話ずつ分ける"
        ])

        layout.addWidget(
            self.save_mode_combo
        )

        # ボタン
        self.download_btn = QPushButton(
            "ダウンロード開始"
        )

        self.download_btn.clicked.connect(
            self.start_download
        )

        layout.addWidget(
            self.download_btn
        )

        # プログレスバー
        self.progress = QProgressBar()

        layout.addWidget(
            self.progress
        )

        # ログ
        self.log_box = QTextEdit()

        self.log_box.setReadOnly(True)

        layout.addWidget(
            self.log_box
        )

        self.setLayout(layout)

        # デザイン
        self.setStyleSheet("""
QWidget {
    background-color: #e8f5e9;
    color: #202020;
    font-size: 14px;
}

QLabel {
    font-weight: bold;
}

QLineEdit {
    background-color: white;
    border: 1px solid #81c784;
    border-radius: 6px;
    padding: 6px;
}

QTextEdit {
    background-color: white;
    border: 1px solid #81c784;
    border-radius: 6px;
}

QPushButton {
    background-color: #81c784;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #66bb6a;
}

QPushButton:pressed {
    background-color: #4caf50;
}

QProgressBar {
    border: 1px solid #81c784;
    border-radius: 6px;
    text-align: center;
    background-color: white;
}

QProgressBar::chunk {
    background-color: #66bb6a;
    border-radius: 6px;
}

QSpinBox,
QDoubleSpinBox,
QComboBox {
    background-color: white;
    border: 1px solid #81c784;
    border-radius: 6px;
    padding: 4px;
}
""")

    def log(self, text):
        self.log_box.append(text)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "保存先選択"
        )

        if folder:
            self.folder_input.setText(
                folder
            )

    def _parse_input(self, raw):
        """作品IDまたはURLを解析し、ncodeを返す。無効な入力ならNone"""
        # URL形式の場合
        url_match = re.match(
            r"^https?://(?P<domain>[^/]+)/(?P<ncode>[a-z0-9]+)/?$",
            raw.strip(),
            re.IGNORECASE
        )

        if url_match:
            domain = url_match.group("domain").lower()
            ncode = url_match.group("ncode").lower()

            if domain != "ncode.syosetu.com":
                QMessageBox.warning(
                    self,
                    "エラー",
                    f"対応していないサイトです: {domain}\n"
                    f"現在は ncode.syosetu.com のみ対応しています。"
                )
                return None
            return ncode

        # ncode直入力
        ncode = raw.strip().lower()
        if re.match(r"^[a-z0-9]+$", ncode):
            return ncode

        QMessageBox.warning(
            self,
            "エラー",
            "作品IDの形式が正しくありません。\n"
            "例: n9669bk\n"
            "またはURL: https://ncode.syosetu.com/n9669bk/"
        )
        return None

    def start_download(self):
        raw_input = (
            self.ncode_input.text()
            .strip()
        )

        save_dir = (
            self.folder_input.text()
            .strip()
        )

        max_workers = (
            self.worker_spin.value()
        )

        delay = (
            self.delay_spin.value()
        )

        save_mode = (
            self.save_mode_combo.currentText()
        )

        if not raw_input:
            QMessageBox.warning(
                self,
                "エラー",
                "作品IDまたはURLを入力してください"
            )
            return

        # URLかncodeかを判定
        ncode = self._parse_input(raw_input)
        if not ncode:
            return

        if not save_dir:
            QMessageBox.warning(
                self,
                "エラー",
                "保存先を選択してください"
            )
            return

        self.download_btn.setEnabled(
            False
        )

        self.progress.setValue(0)

        self.log_box.clear()

        self.signals = DownloaderSignals()

        self.signals.log.connect(
            self.log
        )

        self.signals.progress.connect(
            self.progress.setValue
        )

        self.signals.finished.connect(
            self.finished
        )

        self.signals.error.connect(
            self.error
        )

        downloader = NarouDownloader(
            ncode,
            save_dir,
            max_workers,
            delay,
            save_mode,
            self.signals
        )

        thread = threading.Thread(
            target=downloader.run,
            daemon=True
        )

        thread.start()

    def finished(self, path):
        self.log(
            f"\n保存完了\n{path}"
        )

        QMessageBox.information(
            self,
            "完了",
            f"保存完了\n\n{path}"
        )

        self.download_btn.setEnabled(
            True
        )

    def error(self, text):
        QMessageBox.critical(
            self,
            "エラー",
            text
        )

        self.download_btn.setEnabled(
            True
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)

    window = MainWindow()

    window.show()

    sys.exit(app.exec())
