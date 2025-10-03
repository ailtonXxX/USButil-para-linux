import sys, os, subprocess, shutil
from pathlib import Path
from PyQt5.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QComboBox, QTableWidget,
                             QTableWidgetItem, QFileDialog, QProgressBar,
                             QMessageBox, QHeaderView, QCheckBox, QAbstractItemView)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QBrush, QIcon

# ------------------- Verifica root -------------------
if os.geteuid() != 0:
    app = QApplication(sys.argv)
    QMessageBox.critical(None,"Permissão","Este programa precisa ser executado como root!\nUse: sudo python3 USBUtil.py")
    sys.exit(1)

# ------------------- Funções utilitárias -------------------
def run_cmd(cmd):
    return subprocess.getoutput(cmd)

def get_removable_devices():
    out = run_cmd("lsblk -o NAME,MODEL,SIZE,RM,MOUNTPOINT -P")
    devices = []
    for line in out.splitlines():
        line = line.strip()
        if not line: continue
        props = {}
        for token in line.split():
            if '=' not in token: continue
            try: k,v = token.split('=',1); props[k]=v.strip('"')
            except ValueError: continue
        if props.get("RM")=="1": devices.append(props)
    return devices

def format_drive(dev):
    try:
        os.system(f"umount /dev/{dev}* 2>/dev/null")
        r = os.system(f"mkfs.vfat -F 32 /dev/{dev}")
        return r==0
    except: return False

def extract_game_id(iso_path):
    try:
        with open(iso_path,"rb") as f:
            buf = f.read(5*1024*1024)
            idx = buf.find(b"cdrom0:\\")
            if idx!=-1:
                start = idx+8
                gid = b""
                for b in buf[start:]:
                    if b in (0,ord(';'),ord('\\'),ord(' ')): break
                    gid += bytes([b])
                return gid.decode(errors="ignore")
        return "UNKNOWN_ID"
    except: return "UNKNOWN_ID"

def append_ulcfg(cfg_path, game_id, game_name, type_flag):
    entries=[]
    if os.path.exists(cfg_path):
        with open(cfg_path,"rb") as f:
            while True:
                chunk = f.read(64)
                if len(chunk)!=64: break
                entries.append(bytearray(chunk))
    entry = bytearray(64)
    entry[:8] = game_id.encode()[:8].ljust(8,b'\0')
    entry[8:40] = game_name.encode()[:32].ljust(32,b'\0')
    entry[40] = type_flag
    entries.append(entry)
    with open(cfg_path,"wb") as f:
        for e in entries: f.write(e)

def get_game_icon(game_id):
    icon_path_png = Path("icons")/f"{game_id}.png"
    icon_path_ico = Path("icons")/f"{game_id}.ico"
    if icon_path_png.exists(): return QIcon(str(icon_path_png))
    elif icon_path_ico.exists(): return QIcon(str(icon_path_ico))
    else: return None

# ------------------- Thread de cópia/extracao -------------------
class CopyThread(QThread):
    progress_game = pyqtSignal(int,int)
    progress_total = pyqtSignal(float)
    finished = pyqtSignal()

    def __init__(self, rows, isos, mount_path):
        super().__init__()
        self.rows = rows
        self.isos = isos
        self.mount = Path(mount_path)
        self.running = True

    def run(self):
        total_size = sum(Path(iso).stat().st_size for iso in self.isos)
        copied_total = 0

        dvd_dir = self.mount/"DVD"
        try: dvd_dir.mkdir(exist_ok=True)
        except PermissionError:
            QMessageBox.critical(None,"Erro","Não é possível escrever no pendrive.\nVerifique permissões ou formate o dispositivo.")
            return

        for idx,row in enumerate(self.rows):
            iso = self.isos[idx]
            if not self.running: break

            iso_size = Path(iso).stat().st_size
            game_id = extract_game_id(iso)
            tipo = 0x00 if iso_size/(1024*1024)<=700 else 0x01

            tmp_mount = Path(f"/tmp/usbutil_iso_{game_id}")
            if tmp_mount.exists(): shutil.rmtree(tmp_mount)
            tmp_mount.mkdir(parents=True, exist_ok=True)

            r = os.system(f"7z x '{iso}' -o'{tmp_mount}' >/dev/null 2>&1")
            if r != 0:
                QMessageBox.critical(None,"Erro",f"Falha ao extrair ISO {iso}")
                shutil.rmtree(tmp_mount)
                continue

            dest = dvd_dir
            for root, dirs, files in os.walk(tmp_mount):
                rel = Path(root).relative_to(tmp_mount)
                for d in dirs:
                    try: (dest/rel/d).mkdir(parents=True, exist_ok=True)
                    except PermissionError: continue
                for f in files:
                    src_file = Path(root)/f
                    dest_file = dest/rel/f
                    try:
                        max_part = 700*1024*1024
                        if src_file.stat().st_size > max_part:
                            with open(src_file,"rb") as sf:
                                part_num=0
                                while True:
                                    buf=sf.read(max_part)
                                    if not buf: break
                                    out_file=dest/rel/f".part{part_num:02d}"
                                    out_file.parent.mkdir(parents=True, exist_ok=True)
                                    with open(out_file,"wb") as out: out.write(buf)
                                    part_num+=1
                        else:
                            dest_file.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file,dest_file)
                    except Exception as e: print(f"Erro copiando {src_file}: {e}"); continue

                    copied_total += src_file.stat().st_size
                    percent_total = min(1.0, copied_total/total_size)
                    percent_file = int((copied_total*100)/total_size)
                    self.progress_game.emit(row, percent_file)
                    self.progress_total.emit(percent_total)

            append_ulcfg(self.mount/"ul.cfg", game_id, Path(iso).name, tipo)
            shutil.rmtree(tmp_mount)

        self.finished.emit()

# ------------------- GUI -------------------
class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("USBUtil Linux Moderno - ROOT")
        self.resize(1100,700)
        self.dark_mode = True
        self.isos=[]
        self.copy_thread=None

        vbox = QVBoxLayout()
        hbox_top = QHBoxLayout()
        self.combo_dev = QComboBox()
        self.btn_refresh = QPushButton("Atualizar")
        self.btn_format = QPushButton("Formatar")
        self.btn_toggle = QPushButton("Dark/Light")
        hbox_top.addWidget(self.combo_dev)
        hbox_top.addWidget(self.btn_refresh)
        hbox_top.addWidget(self.btn_format)
        hbox_top.addWidget(self.btn_toggle)
        vbox.addLayout(hbox_top)

        self.table_iso = QTableWidget()
        self.table_iso.setColumnCount(7)
        self.table_iso.setHorizontalHeaderLabels(["Copiar","Ícone","Nome","Tamanho (MB)","ID do jogo","Tipo","Progresso"])
        self.table_iso.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_iso.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_iso.setSelectionBehavior(QAbstractItemView.SelectRows)
        vbox.addWidget(self.table_iso)

        hbox_btns = QHBoxLayout()
        self.btn_select_dir = QPushButton("Selecionar Pasta de ISOs")
        self.btn_add = QPushButton("Gravar no Pendrive")
        self.btn_list = QPushButton("Listar no Pendrive")
        hbox_btns.addWidget(self.btn_select_dir)
        hbox_btns.addWidget(self.btn_add)
        hbox_btns.addWidget(self.btn_list)
        vbox.addLayout(hbox_btns)

        self.progress_total = QProgressBar()
        vbox.addWidget(QLabel("Progresso total"))
        vbox.addWidget(self.progress_total)

        self.setLayout(vbox)

        self.btn_refresh.clicked.connect(self.refresh_devices)
        self.btn_format.clicked.connect(self.format_device)
        self.btn_toggle.clicked.connect(self.toggle_theme)
        self.btn_select_dir.clicked.connect(self.select_dir)
        self.btn_add.clicked.connect(self.add_batch)
        self.btn_list.clicked.connect(self.list_ulcfg)

        self.refresh_devices()
        self.apply_theme()

    def refresh_devices(self):
        self.combo_dev.clear()
        self.devices = get_removable_devices()
        for d in self.devices:
            text=f"{d['NAME']} - {d.get('MODEL','Desconhecido')} - {d.get('SIZE','?')} - {d.get('MOUNTPOINT','(não montado)')}"
            self.combo_dev.addItem(text)

    def get_selected_mount(self):
        idx = self.combo_dev.currentIndex()
        if idx<0 or idx>=len(self.devices): return ""
        mount = self.devices[idx].get('MOUNTPOINT')
        dev = self.devices[idx]['NAME']
        if not mount or mount=="(não montado)":
            mount_point = Path(f"/media/{os.getlogin()}/{dev}")
            mount_point.mkdir(parents=True, exist_ok=True)
            r = os.system(f"mount -o rw /dev/{dev} {mount_point} 2>/dev/null")
            if r==0: mount = str(mount_point); self.devices[idx]['MOUNTPOINT']=mount
            else: mount=""
        return mount

    def get_selected_dev(self):
        idx = self.combo_dev.currentIndex()
        if idx<0 or idx>=len(self.devices): return ""
        return self.devices[idx]['NAME']

    def format_device(self):
        dev = self.get_selected_dev()
        if not dev: QMessageBox.warning(self,"Aviso","Nenhum pendrive selecionado"); return
        r = QMessageBox.question(self,"Confirmação",f"Deseja formatar {dev} em FAT32? Todos os dados serão apagados.")
        if r != QMessageBox.Yes: return
        if format_drive(dev): QMessageBox.information(self,"Sucesso","Pendrive formatado com sucesso")
        else: QMessageBox.critical(self,"Erro","Falha ao formatar")
        self.refresh_devices()

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.apply_theme()

    def apply_theme(self):
        if self.dark_mode:
            self.setStyleSheet("""
            QWidget { background-color: #2b2b2b; color: #fff; }
            QTableWidget { background-color: #3b3b3b; gridline-color: #555; }
            QHeaderView::section { background-color: #444; color: #fff; }
            QProgressBar { border: 1px solid #555; text-align: center; }
            QProgressBar::chunk { background-color: #37a; }
            """)
        else: self.setStyleSheet("")

    def select_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta de ISOs")
        if not folder: return
        self.isos=[]
        self.table_iso.setRowCount(0)
        for p in Path(folder).rglob("*.iso"): self.add_iso_to_table(p)

    def add_iso_to_table(self, iso_path):
        row=self.table_iso.rowCount()
        self.table_iso.insertRow(row)
        chk = QCheckBox(); chk.setChecked(True); self.table_iso.setCellWidget(row,0,chk)
        gid = extract_game_id(str(iso_path))
        icon = get_game_icon(gid)
        icon_item = QTableWidgetItem()
        if icon: icon_item.setIcon(icon)
        else: icon_item.setText("💿" if iso_path.stat().st_size/(1024*1024)<=700 else "📀")
        self.table_iso.setItem(row,1,icon_item)
        self.table_iso.setItem(row,2,QTableWidgetItem(iso_path.name))
        size_mb=int(iso_path.stat().st_size/(1024*1024))
        self.table_iso.setItem(row,3,QTableWidgetItem(str(size_mb)))
        self.table_iso.setItem(row,4,QTableWidgetItem(gid))
        tipo_item = QTableWidgetItem("CD" if size_mb<=700 else "DVD")
        tipo_item.setBackground(QBrush(QColor(173,216,230) if size_mb<=700 else QColor(216,191,216)))
        self.table_iso.setItem(row,5,tipo_item)
        prog = QProgressBar(); prog.setValue(0); self.table_iso.setCellWidget(row,6,prog)
        for col in range(6):
            item = self.table_iso.item(row,col)
            if item: item.setToolTip(str(iso_path))
        self.isos.append(str(iso_path))

    def add_batch(self):
        self.refresh_devices()
        mount = self.get_selected_mount()
        if not mount: QMessageBox.warning(self,"Aviso","Não é possível gravar. Pendrive não montado ou somente leitura."); return
        rows = [i for i in range(self.table_iso.rowCount()) if self.table_iso.cellWidget(i,0).isChecked()]
        if not rows: QMessageBox.warning(self,"Aviso","Selecione pelo menos um jogo"); return
        self.copy_thread = CopyThread(rows,[self.isos[i] for i in rows],mount)
        self.copy_thread.progress_game.connect(lambda r,p:self.table_iso.cellWidget(r,6).setValue(p))
        self.copy_thread.progress_total.connect(lambda p:self.progress_total.setValue(int(p*100)))
        self.copy_thread.finished.connect(lambda: QMessageBox.information(self,"Concluído","Cópia finalizada"))
        self.copy_thread.start()

    def list_ulcfg(self):
        mount = self.get_selected_mount()
        cfg_path = Path(mount)/"ul.cfg"
        if not cfg_path.exists(): QMessageBox.information(self,"Info","Arquivo ul.cfg não encontrado"); return
        with open(cfg_path,"rb") as f: content=f.read()
        games=[]
        for i in range(0,len(content),64):
            chunk = content[i:i+64]
            gid = chunk[:8].decode(errors="ignore").strip("\0")
            name = chunk[8:40].decode(errors="ignore").strip("\0")
            type_flag = "CD" if chunk[40]==0 else "DVD"
            games.append(f"{gid} - {name} ({type_flag})")
        QMessageBox.information(self,"ul.cfg","\n".join(games))

# ------------------- Main -------------------
if __name__=="__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())