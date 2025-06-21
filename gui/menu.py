import os
from PySide6.QtWidgets import (
   QApplication, QWidget, QMenu, 
   QMessageBox, QStyle
)
from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon

class DownloadContext(QMenu):
    do_delete = Signal()
    def __init__(self, parent:QWidget, task:dict):
        super().__init__(parent)
        self.task = task
        app_style = QApplication.style()

        openAction = self.addAction(app_style.standardIcon(QStyle.SP_FileIcon), "Open File")
        openAction.triggered.connect(self.openFile)

        openDirAction = self.addAction(app_style.standardIcon(QStyle.SP_DirOpenIcon), "Show in Folder")
        openDirAction.triggered.connect(self.openDir)

        deleteAction = self.addAction(app_style.standardIcon(QStyle.SP_TrashIcon), "Delete File")
        deleteAction.triggered.connect(self.deleteFile)

    def openFile(self):
        if not os.path.exists(self.task['path']):
            QMessageBox.information(None, "Not Found",
                "The selected file is missing."
            )
            return
        url = QUrl.fromLocalFile(self.task['path'])
        QDesktopServices.openUrl(url)

    def openDir(self):
        dir_path = os.path.dirname(self.task['path'])
        if not os.path.isdir(dir_path):
            QMessageBox.information(None, "Not Found",
                "The selected folder is missing."
            )
            return
        url = QUrl.fromLocalFile(dir_path)
        QDesktopServices.openUrl(url)

    def deleteFile(self):
        self.do_delete.emit()
