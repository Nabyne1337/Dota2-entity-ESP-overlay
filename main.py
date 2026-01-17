import pymem
import pymem.process
import sys
import struct
import ctypes
from ctypes import wintypes
import math

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QColor, QFont

DW_ENTITY_LIST = 0x5EE3018
DW_LOCAL_PLAYERS = 0x60FDC68
DW_VIEW_MATRIX = 0x61132D0

OFFSET_HEALTH = 0x354
OFFSET_MAX_HEALTH = 0x350
OFFSET_TEAM = 0x3F3
OFFSET_GAME_SCENE_NODE = 0x338

OFFSET_ABS_ORIGIN = 0xD0

OFFSET_LEVEL = 0xC64
OFFSET_MANA = 0xCBC
OFFSET_MAX_MANA = 0xCC0

OFFSET_ASSIGNED_HERO = 0x90C

ENTITY_IDENTITY_SIZE = 112

PROCESS_NAME = "dota2.exe"

user32 = ctypes.windll.user32


def get_screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def get_dota_window():
    hwnd = user32.FindWindowW(None, "Dota 2")
    if hwnd == 0:
        hwnd = user32.FindWindowW(None, "DOTA 2")
    return hwnd


def get_window_rect(hwnd):
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    
    point = wintypes.POINT()
    point.x = rect.left
    point.y = rect.top
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    
    return point.x, point.y, rect.right - rect.left, rect.bottom - rect.top


class DotaMemoryReader:
    
    def __init__(self):
        self.pm = None
        self.client_base = None
        self.local_team = 0
        
    def connect(self):
        try:
            self.pm = pymem.Pymem(PROCESS_NAME)
            for module in self.pm.list_modules():
                if module.name.lower() == "client.dll":
                    self.client_base = module.lpBaseOfDll
                    break
            return self.client_base is not None
        except Exception:
            return False
    
    def read_vector3(self, address):
        try:
            x = self.pm.read_float(address)
            y = self.pm.read_float(address + 4)
            z = self.pm.read_float(address + 8)
            return (x, y, z)
        except:
            return (0.0, 0.0, 0.0)
    
    def read_view_matrix(self):
        try:
            matrix = []
            addr = self.client_base + DW_VIEW_MATRIX
            for i in range(12):
                matrix.append(self.pm.read_float(addr + i * 4))
            return matrix
        except:
            return None
    
    def get_local_team(self):
        try:
            local_players_ptr = self.pm.read_longlong(self.client_base + DW_LOCAL_PLAYERS)
            if local_players_ptr == 0:
                return 0
            
            hero_handle = self.pm.read_uint(local_players_ptr + OFFSET_ASSIGNED_HERO)
            if hero_handle == 0 or hero_handle == 0xFFFFFFFF:
                return 0
            
            entity_list = self.pm.read_longlong(self.client_base + DW_ENTITY_LIST)
            chunk_index = (hero_handle & 0x7FFF) >> 9
            index_in_chunk = hero_handle & 0x1FF
            
            chunk_ptr = self.pm.read_longlong(entity_list + chunk_index * 8)
            if chunk_ptr == 0:
                return 0
            
            entity = self.pm.read_longlong(chunk_ptr + index_in_chunk * ENTITY_IDENTITY_SIZE)
            if entity == 0:
                return 0
            
            return self.pm.read_uchar(entity + OFFSET_TEAM)
        except:
            return 0
    
    def get_entities(self):
        entities = []
        
        if self.local_team == 0:
            self.local_team = self.get_local_team()
        
        try:
            entity_list = self.pm.read_longlong(self.client_base + DW_ENTITY_LIST)
            if entity_list == 0:
                return entities
            
            for i in range(1, 2048):
                entity = self._get_entity_by_index(entity_list, i)
                if entity is None:
                    continue
                
                info = self._get_entity_info(entity)
                if info is not None:
                    entities.append(info)
            
        except Exception:
            pass
        
        return entities
    
    def _get_entity_by_index(self, entity_list, index):
        try:
            chunk_index = index >> 9
            index_in_chunk = index & 0x1FF
            
            chunk_ptr = self.pm.read_longlong(entity_list + chunk_index * 8)
            if chunk_ptr == 0:
                return None
            
            entity = self.pm.read_longlong(chunk_ptr + index_in_chunk * ENTITY_IDENTITY_SIZE)
            return entity if entity != 0 else None
        except:
            return None
    
    def _get_entity_info(self, entity):
        try:
            health = self.pm.read_int(entity + OFFSET_HEALTH)
            max_health = self.pm.read_int(entity + OFFSET_MAX_HEALTH)
            
            if max_health <= 0 or health < 0 or health > max_health + 1000:
                return None
            
            if health <= 0:
                return None
            
            team = self.pm.read_uchar(entity + OFFSET_TEAM)
            
            if team < 2:
                return None
            
            scene_node = self.pm.read_longlong(entity + OFFSET_GAME_SCENE_NODE)
            if scene_node == 0:
                return None
            
            pos = self.read_vector3(scene_node + OFFSET_ABS_ORIGIN)
            
            if pos[0] == 0 and pos[1] == 0 and pos[2] == 0:
                return None
            
            try:
                mana = self.pm.read_float(entity + OFFSET_MANA)
                max_mana = self.pm.read_float(entity + OFFSET_MAX_MANA)
            except:
                mana = 0
                max_mana = 0
            
            is_hero = max_mana > 0 and max_health > 400
            is_ally = team == self.local_team
            
            return {
                'pos': pos,
                'health': health,
                'max_health': max_health,
                'mana': mana,
                'max_mana': max_mana,
                'team': team,
                'is_hero': is_hero,
                'is_ally': is_ally
            }
        except:
            return None


class OverlayWindow(QWidget):
    
    def __init__(self):
        super().__init__()
        
        self.reader = DotaMemoryReader()
        self.entities = []
        self.dota_hwnd = 0
        self.window_rect = (0, 0, 1920, 1080)
        self.view_matrix = None
        
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool |
            Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        
        screen_w, screen_h = get_screen_size()
        self.setGeometry(0, 0, screen_w, screen_h)
        
        self.font_main = QFont("Consolas", 10, QFont.Bold)
        self.font_small = QFont("Consolas", 8)
        
        self.color_ally = QColor(0, 255, 100)
        self.color_enemy = QColor(255, 60, 60)
        self.color_health_bg = QColor(30, 30, 30, 180)
        self.color_mana = QColor(80, 150, 255)
        self.color_text = QColor(255, 255, 255)
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.start(50)
        
        if not self.reader.connect():
            sys.exit(1)
    
    def update_data(self):
        self.dota_hwnd = get_dota_window()
        if self.dota_hwnd:
            self.window_rect = get_window_rect(self.dota_hwnd)
        
        self.view_matrix = self.reader.read_view_matrix()
        
        self.entities = self.reader.get_entities()
        
        self.update()
    
    def world_to_screen(self, world_pos):
        if self.view_matrix is None:
            return None
        
        x, y, z = world_pos
        m = self.view_matrix
        
        screen_x = m[0] * x + m[1] * y + m[2] * z + m[3]
        screen_y = m[4] * x + m[5] * y + m[6] * z + m[7]
        w = m[8] * x + m[9] * y + m[10] * z + m[11]
        
        if w < 0.001:
            return None
        
        inv_w = 1.0 / w
        screen_x *= inv_w
        screen_y *= inv_w
        
        win_x, win_y, win_w, win_h = self.window_rect
        
        screen_x = win_x + (screen_x * 0.5 + 0.5) * win_w
        screen_y = win_y + (0.5 - screen_y * 0.5) * win_h
        
        return int(screen_x), int(screen_y)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        for entity in self.entities:
            self.draw_entity(painter, entity)
    
    def draw_entity(self, painter, entity):
        result = self.world_to_screen(entity['pos'])
        
        if result is None:
            return
        
        screen_x, screen_y = result
        
        if screen_x < 0 or screen_x > self.width() or screen_y < 0 or screen_y > self.height():
            return
        
        color = self.color_ally if entity['is_ally'] else self.color_enemy
        
        bar_width = 60 if entity['is_hero'] else 40
        bar_height = 6 if entity['is_hero'] else 4
        
        x = screen_x - bar_width // 2
        y = screen_y - 30
        
        painter.fillRect(x - 1, y - 1, bar_width + 2, bar_height + 2, self.color_health_bg)
        
        hp_percent = entity['health'] / entity['max_health'] if entity['max_health'] > 0 else 0
        hp_width = int(bar_width * hp_percent)
        painter.fillRect(x, y, hp_width, bar_height, color)
        
        if entity['is_hero'] and entity['max_mana'] > 0:
            mana_y = y + bar_height + 2
            mana_percent = entity['mana'] / entity['max_mana'] if entity['max_mana'] > 0 else 0
            mana_width = int(bar_width * mana_percent)
            
            painter.fillRect(x - 1, mana_y - 1, bar_width + 2, bar_height + 2, self.color_health_bg)
            painter.fillRect(x, mana_y, mana_width, bar_height, self.color_mana)
        
        painter.setFont(self.font_small)
        painter.setPen(self.color_text)
        
        hp_text = f"{entity['health']}/{entity['max_health']}"
        text_y = y - 3
        
        painter.setPen(QColor(0, 0, 0))
        painter.drawText(x + 1, text_y + 1, hp_text)
        
        painter.setPen(self.color_text)
        painter.drawText(x, text_y, hp_text)
        
        coord_y = y + bar_height + (14 if entity['is_hero'] else 8)
        coord_text = f"({int(entity['pos'][0])}, {int(entity['pos'][1])})"
        
        painter.setPen(QColor(0, 0, 0))
        painter.drawText(x + 1, coord_y + 1, coord_text)
        painter.setPen(QColor(200, 200, 200))
        painter.drawText(x, coord_y, coord_text)


def main():
    app = QApplication(sys.argv)
    overlay = OverlayWindow()
    overlay.show()
    
    try:
        sys.exit(app.exec_())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
