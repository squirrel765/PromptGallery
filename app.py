import customtkinter as ctk
from PIL import Image
import os
import json
import threading
import tkinter as tk
from tkinter import messagebox, filedialog
import sqlite3
import sys
from googletrans import Translator
from itertools import zip_longest
from PIL import ImageTk

# --- 유틸리티 함수 및 상수 정의 ---
def setup_directories():
    os.makedirs(THUMBNAIL_DIR, exist_ok=True)

CONFIG_FILE = "config.json"
DB_FILE = "gallery.db"
CACHE_DIR = ".cache"
THUMBNAIL_DIR = os.path.join(CACHE_DIR, "thumbnails")
CUSTOM_TRANSLATIONS_FILE = "custom_translations.json"

# --- 공용 메타데이터 파싱 함수 ---
def parse_image_metadata(image_info):
    parsed_data = {'prompt': '', 'negative_prompt': '', 'others': ''}
    if "parameters" in image_info:
        full_params = image_info['parameters']
        parts = full_params.split("Negative prompt:")
        parsed_data['prompt'] = parts[0].strip()
        if len(parts) > 1:
            neg_parts = parts[1].split("\n", 1)
            parsed_data['negative_prompt'] = neg_parts[0].strip()
            parsed_data['others'] = neg_parts[1].strip() if len(neg_parts) > 1 else ""
    elif "prompt" in image_info:
        try:
            prompt_json = json.loads(image_info.get('prompt', '{}'))
            if not prompt_json: return parsed_data
            sampler_nodes = [n for n in prompt_json.values() if 'KSampler' in n.get('class_type', '')]
            if not sampler_nodes: return parsed_data
            final_sampler_node_data = sampler_nodes[-1]
            parsed_data['prompt'] = trace_comfy_prompt(prompt_json, final_sampler_node_data['inputs']['positive'][0])
            parsed_data['negative_prompt'] = trace_comfy_prompt(prompt_json, final_sampler_node_data['inputs']['negative'][0])
            other_info, sampler_inputs = [], final_sampler_node_data['inputs']
            other_info.extend([f"Seed: {sampler_inputs.get('seed')}", f"Steps: {sampler_inputs.get('steps')}", f"CFG: {sampler_inputs.get('cfg')}", f"Sampler: {sampler_inputs.get('sampler_name')}", f"Scheduler: {sampler_inputs.get('scheduler')}", f"Denoise: {sampler_inputs.get('denoise')}"])
            if 'model' in sampler_inputs:
                model_name = trace_comfy_input(prompt_json, sampler_inputs['model'][0], "CheckpointLoaderSimple", "ckpt_name")
                if model_name: other_info.append(f"Model: {model_name}")
            parsed_data['others'] = "\n".join(info for info in other_info if info)
        except (json.JSONDecodeError, TypeError, KeyError): pass
    return parsed_data

def trace_comfy_prompt(prompt_json, start_node_id_str):
    start_node_id = str(start_node_id_str)
    if start_node_id not in prompt_json: return ""
    node_data = prompt_json[start_node_id]; class_type, inputs = node_data.get('class_type', ''), node_data.get('inputs', {})
    if 'CLIPTextEncode' in class_type: return trace_comfy_prompt(prompt_json, inputs['text'][0]) if isinstance(inputs.get('text'), list) else inputs.get('text', '')
    elif 'PromptSwitchHub' in class_type:
        prompts = []
        for i in range(1, 8):
            if inputs.get(f'enabled_{i}', False):
                prompt_input = inputs.get(f'prompt_{i}')
                if prompt_input:
                    if isinstance(prompt_input, list): prompts.append(trace_comfy_prompt(prompt_json, prompt_input[0]))
                    else: prompts.append(str(prompt_input))
        return ", ".join(p for p in prompts if p)
    for v in inputs.values():
        if isinstance(v, list) and v:
            res = trace_comfy_prompt(prompt_json, v[0]);
            if res: return res
    return ""

def trace_comfy_input(prompt_json, start_node_id_str, target_class, target_input_key):
    start_node_id = str(start_node_id_str)
    if start_node_id not in prompt_json: return None
    node_data = prompt_json[start_node_id]; class_type, inputs = node_data.get('class_type', ''), node_data.get('inputs', {})
    if target_class in class_type: return inputs.get(target_input_key)
    for v in inputs.values():
        if isinstance(v, list) and v:
            res = trace_comfy_input(prompt_json, v[0], target_class, target_input_key);
            if res: return res
    return None

class DatabaseManager:
    def __init__(self, db_file):
        self.db_file = db_file
        self.setup_tables()
    def _get_connection(self):
        return sqlite3.connect(self.db_file, timeout=10)
    def _execute(self, query, params=(), fetch=None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetch == 'one': return cursor.fetchone()
            if fetch == 'all': return cursor.fetchall()
            conn.commit()
    def _executemany(self, query, params):
        with self._get_connection() as conn:
            conn.cursor().executemany(query, params)
            conn.commit()
    def setup_tables(self):
        self._execute('''CREATE TABLE IF NOT EXISTS images (path TEXT PRIMARY KEY, positive_prompt TEXT, negative_prompt TEXT, other_params TEXT, is_favorite INTEGER DEFAULT 0, timestamp REAL)''')
        self._execute('''CREATE TABLE IF NOT EXISTS albums (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, position INTEGER)''')
        self._execute('''CREATE TABLE IF NOT EXISTS album_images (album_id INTEGER, image_path TEXT, FOREIGN KEY (album_id) REFERENCES albums (id) ON DELETE CASCADE, FOREIGN KEY (image_path) REFERENCES images (path) ON DELETE CASCADE, PRIMARY KEY (album_id, image_path))''')
        self._execute('''CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL)''')
        self._execute('''CREATE TABLE IF NOT EXISTS image_tags (image_path TEXT, tag_id INTEGER, FOREIGN KEY (image_path) REFERENCES images (path) ON DELETE CASCADE, FOREIGN KEY (tag_id) REFERENCES tags (id) ON DELETE CASCADE, PRIMARY KEY (image_path, tag_id))''')
    def sync_files(self, file_paths):
        db_paths = set(row[0] for row in self._execute("SELECT path FROM images", fetch='all'))
        fs_paths = set(file_paths)
        to_add, to_remove = fs_paths - db_paths, db_paths - fs_paths
        if to_add: self._executemany("INSERT OR IGNORE INTO images (path) VALUES (?)", [(p,) for p in to_add])
        if to_remove: self._executemany("DELETE FROM images WHERE path=?", [(p,) for p in to_remove])
    def get_all_image_paths(self): return [row[0] for row in self._execute("SELECT path FROM images ORDER BY timestamp DESC NULLS LAST", fetch='all')]
    def update_image_cache(self, path, parsed_data, timestamp): self._execute("UPDATE images SET positive_prompt=?, negative_prompt=?, other_params=?, timestamp=? WHERE path=?", (parsed_data['prompt'], parsed_data['negative_prompt'], parsed_data['others'], timestamp, path))
    def get_parsed_prompts(self, path): return self._execute("SELECT positive_prompt, negative_prompt FROM images WHERE path=?", (path,), fetch='one') or ("", "")
    def get_image_data(self, path): return self._execute("SELECT is_favorite, timestamp FROM images WHERE path=?", (path,), fetch='one') or (0, 0)
    def set_favorite(self, path, is_fav): self._execute("UPDATE images SET is_favorite=? WHERE path=?", (1 if is_fav else 0, path))
    def get_favorites(self): return {row[0] for row in self._execute("SELECT path FROM images WHERE is_favorite=1", fetch='all')}
    def get_albums(self): return self._execute("SELECT id, name FROM albums ORDER BY position", fetch='all')
    def add_album(self, name):
        max_pos = self._execute("SELECT MAX(position) FROM albums", fetch='one')[0] or 0
        self._execute("INSERT INTO albums (name, position) VALUES (?, ?)", (name, max_pos + 1))
    def rename_album(self, album_id, new_name): self._execute("UPDATE albums SET name=? WHERE id=?", (new_name, album_id))
    def delete_album(self, album_id): self._execute("DELETE FROM albums WHERE id=?", (album_id,))
    def get_album_images(self, album_id): return {row[0] for row in self._execute("SELECT image_path FROM album_images WHERE album_id=?", (album_id,), fetch='all')}
    def add_image_to_album(self, album_id, path): self._execute("INSERT OR IGNORE INTO album_images (album_id, image_path) VALUES (?, ?)", (album_id, path))
    def remove_image_from_album(self, album_id, path): self._execute("DELETE FROM album_images WHERE album_id=? AND image_path=?", (album_id, path))
    def get_all_tags(self): return self._execute("SELECT id, name FROM tags ORDER BY name", fetch='all')
    def get_image_tags(self, path): return self._execute("SELECT t.id, t.name FROM tags t JOIN image_tags it ON t.id = it.tag_id WHERE it.image_path = ?", (path,), fetch='all')
    def add_tag_to_image(self, path, tag_name):
        tag_name = tag_name.strip().lower()
        if not tag_name: return
        self._execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
        tag_id = self._execute("SELECT id FROM tags WHERE name=?", (tag_name,), fetch='one')[0]
        self._execute("INSERT OR IGNORE INTO image_tags (image_path, tag_id) VALUES (?, ?)", (path, tag_id))
    def remove_tag_from_image(self, path, tag_id): self._execute("DELETE FROM image_tags WHERE image_path=? AND tag_id=?", (path, tag_id))
    def delete_tag(self, tag_id): self._execute("DELETE FROM tags WHERE id=?", (tag_id,))
    def rename_tag(self, tag_id, new_name): self._execute("UPDATE tags SET name=? WHERE id=?", (new_name, tag_id))
    def get_images_by_tag(self, tag_id): return {row[0] for row in self._execute("SELECT image_path FROM image_tags WHERE tag_id=?", (tag_id,), fetch='all')}
    def get_image_paths_with_tags(self, tag_names: list):
        if not tag_names: return set()
        placeholders = ','.join('?' for _ in tag_names)
        query = f""" SELECT DISTINCT image_path FROM image_tags WHERE tag_id IN (SELECT id FROM tags WHERE name IN ({placeholders})) """
        return {row[0] for row in self._execute(query, tuple(tag_names), fetch='all')}
    def get_tag_id_by_name(self, name):
        result = self._execute("SELECT id FROM tags WHERE name = ?", (name,), fetch='one')
        return result[0] if result else None

class TranslationWindow(ctk.CTkToplevel):
    def __init__(self, parent, original_text, translated_map):
        super().__init__(parent)
        self.transient(parent); self.grab_set(); self.title("번역 결과")
        self.geometry("600x400")
        
        self.app = parent.gallery_app
        self.translation_entries = {}

        scrollable_frame = ctk.CTkScrollableFrame(self)
        scrollable_frame.pack(fill="both", expand=True, padx=10, pady=10)
        scrollable_frame.grid_columnconfigure(0, weight=1)
        scrollable_frame.grid_columnconfigure(2, weight=1)
        
        for i, (orig, trans) in enumerate(translated_map.items()):
            ctk.CTkLabel(scrollable_frame, text=orig, anchor="w").grid(row=i, column=0, sticky="ew", padx=5)
            ctk.CTkLabel(scrollable_frame, text="->").grid(row=i, column=1, padx=10)
            trans_entry = ctk.CTkEntry(scrollable_frame)
            trans_entry.grid(row=i, column=2, sticky="ew", padx=5)
            trans_entry.insert(0, trans)
            self.translation_entries[orig] = trans_entry
        
        ctk.CTkButton(self, text="수정된 번역을 사전에 저장", command=self.save_to_dictionary).pack(pady=10)

    def save_to_dictionary(self):
        for orig, entry_widget in self.translation_entries.items():
            self.app.translator.custom_dict[orig] = entry_widget.get()
        self.app.translator.save_custom_translations()
        messagebox.showinfo("저장 완료", "번역 내용이 사용자 사전에 저장되었습니다.")
        self.destroy()

class TranslatorService:
    def __init__(self, app):
        self.app = app
        self.custom_dict = self.load_custom_translations()
    def load_custom_translations(self):
        try:
            with open(CUSTOM_TRANSLATIONS_FILE, 'r', encoding='utf-8') as f: return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            default_dict = {"1girl": "소녀 1명", "masterpiece": "걸작", "best quality": "최고 품질"}
            self.save_custom_translations(default_dict)
            return default_dict
    def save_custom_translations(self, data=None):
        if data is None: data = self.custom_dict
        with open(CUSTOM_TRANSLATIONS_FILE, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)
    def translate(self, text_to_translate):
        original_parts = [p.strip() for p in text_to_translate.replace('\n',',').split(',') if p.strip()]
        if not original_parts: return {}, "Empty"
        
        translated_map, parts_for_google = {}, []
        for part in original_parts:
            if part in self.custom_dict:
                translated_map[part] = self.custom_dict[part]
            else:
                parts_for_google.append(part)
        
        if parts_for_google:
            try:
                translator = Translator()
                for part in parts_for_google:
                    translated_map[part] = translator.translate(part, dest='ko').text
            except Exception as e:
                messagebox.showerror("Google 번역 오류", f"오류: {e}")
                for part in parts_for_google:
                    translated_map[part] = part
        
        final_map = {orig: translated_map[orig] for orig in original_parts}
        return final_map, "Hybrid"

class DetailWindow(ctk.CTkToplevel):
    def __init__(self, parent, file_path):
        super().__init__(parent)
        self.transient(parent); self.grab_set()
        self.gallery_app, self.db, self.file_path = parent, parent.db, file_path
        self.title(f"상세 정보: {os.path.basename(file_path)}"); self.geometry("1300x800"); self.minsize(1000, 600)
        self.resize_job = None
        
        try:
            self.pil_image = Image.open(self.file_path)
        except Exception as e:
            print(f"Error opening image {file_path}: {e}")
            self.pil_image = None
        
        self.grid_columnconfigure(0, weight=1); self.grid_columnconfigure(1, weight=1); self.grid_rowconfigure(0, weight=1)
        
        self.image_container_frame = ctk.CTkFrame(self)
        self.image_container_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.image_container_frame.grid_rowconfigure(0, weight=1)
        self.image_container_frame.grid_columnconfigure(0, weight=1)
        
        self.image_label = ctk.CTkLabel(self.image_container_frame, text="")
        # *** THIS IS THE FIX ***
        # The label now sticks to all sides of its container, allowing it to resize properly.
        self.image_label.grid(row=0, column=0, sticky="nsew",  padx=0, pady=0) 
        self.image_container_frame.bind("<Configure>", self.on_resize)
        
        right_panel = ctk.CTkScrollableFrame(self)
        right_panel.grid(row=0, column=1, padx=(0,10), pady=10, sticky="nsew")
        right_panel.grid_columnconfigure(0, weight=1)
        
        self.parsed_data = parse_image_metadata(self.pil_image.info if self.pil_image else {})
        self.create_info_widgets(right_panel)

    def on_resize(self, event):
        if self.resize_job:
            self.after_cancel(self.resize_job)
        self.resize_job = self.after(100, lambda: self.perform_resize(event))

    def perform_resize(self, event):
        if not self.pil_image:
            return

        # Get container size
        available_width = self.image_container_frame.winfo_width()
        available_height = self.image_container_frame.winfo_height()

        if available_width <= 1 or available_height <= 1:
            return

        img_w, img_h = self.pil_image.size
        img_aspect = img_w / img_h

        new_w = available_width
        new_h = int(new_w / img_aspect)

        if new_h > available_height:
            new_h = available_height
            new_w = int(new_h * img_aspect)

        new_w = max(50, new_w)
        new_h = max(50, new_h)

        # Resize with PIL and convert to Tkinter-compatible image
        resized = self.pil_image.resize((new_w, new_h), Image.LANCZOS)
        tk_image = ImageTk.PhotoImage(resized)

        self.image_label.configure(image=tk_image)
        self.image_label.image = tk_image  # Keep reference to avoid garbage collection
        
    def create_info_widgets(self, parent):
        action_frame = ctk.CTkFrame(parent, fg_color="transparent")
        action_frame.pack(fill="x", padx=10, pady=10)

        is_fav, _ = self.db.get_image_data(self.file_path)
        self.fav_button = ctk.CTkButton(action_frame, text="★" if is_fav else "☆", command=self.toggle_favorite_detail, width=40)
        self.fav_button.pack(side="left")
        
        ctk.CTkButton(action_frame, text="유사 이미지 찾기", command=lambda: self.gallery_app.find_similar_images(self.file_path)).pack(side="left", padx=10)
        
        tab_view = ctk.CTkTabview(parent)
        tab_view.pack(fill="x", expand=True, padx=10)
        
        self.prompt_box = self.create_tab_with_buttons(tab_view, "Prompt", self.parsed_data.get('prompt', ''), True)
        self.neg_prompt_box = self.create_tab_with_buttons(tab_view, "Negative", self.parsed_data.get('negative_prompt', ''), True)
        self.other_box = self.create_tab_with_buttons(tab_view, "Other", self.parsed_data.get('others', ''), False)
        
        raw_data_tab = tab_view.add("Raw Data")
        raw_tab_view_inner = ctk.CTkTabview(raw_data_tab)
        raw_tab_view_inner.pack(fill="both", expand=True)
        raw_prompt_box = ctk.CTkTextbox(raw_tab_view_inner.add("Prompt (raw)"), wrap="none")
        raw_prompt_box.pack(fill="both", expand=True)
        raw_prompt_box.insert("1.0", json.dumps(self.pil_image.info.get("prompt", {}), indent=4, ensure_ascii=False) if self.pil_image else "")
        raw_workflow_box = ctk.CTkTextbox(raw_tab_view_inner.add("Workflow (raw)"), wrap="none")
        raw_workflow_box.pack(fill="both", expand=True)
        raw_workflow_box.insert("1.0", json.dumps(self.pil_image.info.get("workflow", {}), indent=4, ensure_ascii=False) if self.pil_image else "")
        
        tag_section_frame = ctk.CTkFrame(parent)
        tag_section_frame.pack(fill="x", expand=True, padx=10, pady=10)
        tag_section_frame.grid_columnconfigure(0, weight=1)
        tag_input_frame = ctk.CTkFrame(tag_section_frame)
        tag_input_frame.pack(fill="x", pady=5)
        tag_input_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tag_input_frame, text="태그:").grid(row=0, column=0, sticky="w", padx=5)
        self.tag_entry = ctk.CTkEntry(tag_input_frame, placeholder_text="태그 추가 (엔터)")
        self.tag_entry.grid(row=1, column=0, sticky="ew", padx=5)
        self.tag_entry.bind("<Return>", self.add_tag)
        self.tag_display_frame = ctk.CTkScrollableFrame(tag_section_frame, label_text="이미지 태그", height=150)
        self.tag_display_frame.pack(fill="x", expand=True, pady=5)
        self.display_tags()

    def create_tab_with_buttons(self, tab_view, title, content, show_translate=False):
        tab = tab_view.add(title)
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        
        button_frame = ctk.CTkFrame(tab, fg_color="transparent")
        button_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="e")
        
        if show_translate:
            ctk.CTkButton(button_frame, text="번역", command=lambda t=content: self.translate_text(t)).pack(side="left", padx=(0,5))
        
        ctk.CTkButton(button_frame, text="복사", command=lambda: self.copy_to_clipboard(content)).pack(side="left")
        
        textbox = ctk.CTkTextbox(tab, height=150)
        textbox.grid(row=1, column=0, columnspan=2, padx=5, pady=(0, 5), sticky="nsew")
        textbox.insert("1.0", content)
        return textbox

    def translate_text(self, text_to_translate):
        if not text_to_translate:
            messagebox.showinfo("번역", "번역할 내용이 없습니다.")
            return
        try:
            self.gallery_app.status_label.configure(text="번역 중...")
            self.gallery_app.update_idletasks()
            translated_map, src_lang = self.gallery_app.translator.translate(text_to_translate)
            TranslationWindow(self, text_to_translate, translated_map)
            self.gallery_app.status_label.configure(text="번역 완료.")
        except Exception as e:
            messagebox.showerror("번역 오류", f"번역 중 오류가 발생했습니다:\n{e}")
            self.gallery_app.status_label.configure(text="번역 오류.")
    
    def display_tags(self):
        for widget in self.tag_display_frame.winfo_children(): widget.destroy()
        for i, (tag_id, tag_name) in enumerate(self.db.get_image_tags(self.file_path)):
            frame = ctk.CTkFrame(self.tag_display_frame, fg_color="transparent"); frame.pack(fill="x", pady=2)
            ctk.CTkButton(frame, text="x", width=20, height=20, command=lambda tid=tag_id: self.remove_tag(tid)).pack(side="right")
            ctk.CTkLabel(frame, text=tag_name).pack(side="left", padx=5)
            
    def add_tag(self, event):
        tag_name = self.tag_entry.get()
        if tag_name:
            self.db.add_tag_to_image(self.file_path, tag_name)
            self.tag_entry.delete(0, "end")
            self.display_tags()
            self.gallery_app.update_tag_sidebar()
            
    def remove_tag(self, tag_id):
        self.db.remove_tag_from_image(self.file_path, tag_id)
        self.display_tags()
        self.gallery_app.update_tag_sidebar()
        
    def toggle_favorite_detail(self):
        is_fav = not (self.db.get_image_data(self.file_path)[0] == 1)
        self.db.set_favorite(self.file_path, is_fav)
        self.fav_button.configure(text="★" if is_fav else "☆")
        self.gallery_app.populate_gallery()
        
    def copy_to_clipboard(self, content):
        self.clipboard_clear()
        self.clipboard_append(content)

class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.transient(parent); self.grab_set(); self.title("설정"); self.geometry("500x350"); self.app = parent
        self.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(self, text="이미지 폴더:").grid(row=0, column=0, padx=20, pady=10, sticky="w")
        self.folder_entry = ctk.CTkEntry(self, width=250); self.folder_entry.grid(row=0, column=1, padx=5, pady=10, sticky="ew")
        self.folder_entry.insert(0, self.app.config.get("image_folder", "images"))
        ctk.CTkButton(self, text="찾아보기", command=self.browse_folder).grid(row=0, column=2, padx=5)
        
        ctk.CTkLabel(self, text="썸네일 크기:").grid(row=1, column=0, padx=20, pady=10, sticky="w")
        self.thumb_size_menu = ctk.CTkOptionMenu(self, values=["120x160", "150x200", "180x240", "210x280"])
        self.thumb_size_menu.set(f"{self.app.config.get('thumbnail_width', 180)}x{self.app.config.get('thumbnail_height', 240)}")
        self.thumb_size_menu.grid(row=1, column=1, columnspan=2, padx=5, pady=10, sticky="w")
        
        ctk.CTkLabel(self, text="테마:").grid(row=2, column=0, padx=20, pady=10, sticky="w")
        self.theme_menu = ctk.CTkOptionMenu(self, values=["System", "Light", "Dark"])
        self.theme_menu.set(self.app.config.get("theme", "System"))
        self.theme_menu.grid(row=2, column=1, columnspan=2, padx=5, pady=10, sticky="w")
        
        ctk.CTkLabel(self, text="필터링 태그:").grid(row=3, column=0, padx=20, pady=10, sticky="w")
        self.filter_tags_entry = ctk.CTkEntry(self, placeholder_text="쉼표(,)로 구분 (예: nsfw,loli)")
        self.filter_tags_entry.grid(row=3, column=1, columnspan=2, padx=5, pady=10, sticky="ew")
        self.filter_tags_entry.insert(0, ", ".join(self.app.config.get("filtered_tags", [])))
        
        ctk.CTkButton(self, text="저장 및 다시 시작", command=self.save_and_restart).grid(row=4, column=0, columnspan=3, pady=20)

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_entry.delete(0, "end")
            self.folder_entry.insert(0, folder)
            
    def save_and_restart(self):
        w, h = map(int, self.thumb_size_menu.get().split('x'))
        filtered_tags = [tag.strip().lower() for tag in self.filter_tags_entry.get().split(',') if tag.strip()]
        new_config = {"image_folder": self.folder_entry.get(), "thumbnail_width": w, "thumbnail_height": h, "theme": self.theme_menu.get(), "filtered_tags": filtered_tags}
        with open(CONFIG_FILE, 'w') as f:
            json.dump(new_config, f, indent=4)
        if messagebox.askokcancel("재시작 필요", "설정을 적용하려면 프로그램을 다시 시작해야 합니다.\n지금 다시 시작하시겠습니까?"):
            self.app.restart_program()
        self.destroy()

class ManagementWindow(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.transient(parent)
        self.grab_set()
        self.title("통합 관리")
        self.geometry("700x500")
        self.app, self.db = parent, parent.db

        self.tab_view = ctk.CTkTabview(self)
        self.tab_view.pack(expand=True, fill="both")
        self.album_tab = self.tab_view.add("앨범 관리")
        self.tag_tab = self.tab_view.add("태그 관리")
        self.trans_tab = self.tab_view.add("번역 사전 관리")

        self.populate_album_tab()
        self.populate_tag_tab()
        self.populate_translation_tab()

    def populate_album_tab(self):
        for widget in self.album_tab.winfo_children(): widget.destroy()
        scroll_frame = ctk.CTkScrollableFrame(self.album_tab)
        scroll_frame.pack(expand=True, fill="both", padx=10, pady=10)
        for i, (album_id, name) in enumerate(self.db.get_albums()):
            frame = ctk.CTkFrame(scroll_frame)
            frame.pack(fill="x", pady=2)
            frame.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(frame, text=name).grid(row=0, column=0, padx=5, sticky="w")
            ctk.CTkButton(frame, text="삭제", width=60, fg_color="red", command=lambda aid=album_id, aname=name: self.delete_album(aid, aname)).grid(row=0, column=2, padx=2)
            ctk.CTkButton(frame, text="이름 변경", width=80, command=lambda aid=album_id, old_name=name: self.rename_album(aid, old_name)).grid(row=0, column=1, padx=2)

    def rename_album(self, album_id, old_name):
        dialog = ctk.CTkInputDialog(text=f"'{old_name}'의 새 이름을 입력하세요:", title="앨범 이름 변경")
        new_name = dialog.get_input()
        if new_name and new_name != old_name:
            self.db.rename_album(album_id, new_name)
            self.populate_album_tab()
            self.app.update_view_mode_menu()

    def delete_album(self, album_id, album_name):
        if messagebox.askyesno("앨범 삭제 확인", f"'{album_name}' 앨범을 정말로 삭제하시겠습니까?"):
            self.db.delete_album(album_id)
            self.populate_album_tab()
            self.app.update_view_mode_menu()

    def populate_tag_tab(self):
        for widget in self.tag_tab.winfo_children(): widget.destroy()
        scroll_frame = ctk.CTkScrollableFrame(self.tag_tab)
        scroll_frame.pack(expand=True, fill="both", padx=10, pady=10)
        for i, (tag_id, name) in enumerate(self.db.get_all_tags()):
            frame = ctk.CTkFrame(scroll_frame)
            frame.pack(fill="x", pady=2)
            frame.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(frame, text=name).grid(row=0, column=0, padx=5, sticky="w")
            ctk.CTkButton(frame, text="삭제", width=60, fg_color="red", command=lambda tid=tag_id, tname=name: self.delete_tag(tid, tname)).grid(row=0, column=2, padx=2)
            ctk.CTkButton(frame, text="이름 변경", width=80, command=lambda tid=tag_id, old_name=name: self.rename_tag(tid, old_name)).grid(row=0, column=1, padx=2)

    def rename_tag(self, tag_id, old_name):
        dialog = ctk.CTkInputDialog(text=f"'{old_name}'의 새 이름을 입력하세요:", title="태그 이름 변경")
        new_name = dialog.get_input()
        if new_name and new_name.strip() and new_name != old_name:
            self.db.rename_tag(tag_id, new_name)
            self.populate_tag_tab()
            self.app.update_tag_sidebar()

    def delete_tag(self, tag_id, tag_name):
        if messagebox.askyesno("태그 삭제 확인", f"'{tag_name}' 태그를 정말로 삭제하시겠습니까?\n모든 이미지에서 이 태그가 사라집니다."):
            self.db.delete_tag(tag_id)
            self.populate_tag_tab()
            self.app.update_tag_sidebar()

    def populate_translation_tab(self):
        for widget in self.trans_tab.winfo_children():
            widget.destroy()

        main_frame = ctk.CTkFrame(self.trans_tab, fg_color="transparent")
        main_frame.pack(expand=True, fill="both", padx=10, pady=10)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(2, weight=1)

        filter_frame = ctk.CTkFrame(main_frame)
        filter_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        filter_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(filter_frame, text="검색:").grid(row=0, column=0, padx=5)
        self.trans_search_entry = ctk.CTkEntry(filter_frame, placeholder_text="원본 또는 번역 내용으로 검색...")
        self.trans_search_entry.grid(row=0, column=1, padx=5, sticky="ew")
        self.trans_search_entry.bind("<KeyRelease>", self.filter_translations)

        self.trans_count_label = ctk.CTkLabel(main_frame, text="검색어를 입력하여 사전을 편집하세요.")
        self.trans_count_label.grid(row=1, column=0, sticky="w", padx=5)

        self.trans_scroll_frame = ctk.CTkScrollableFrame(main_frame)
        self.trans_scroll_frame.grid(row=2, column=0, sticky="nsew")
        self.trans_scroll_frame.grid_columnconfigure(1, weight=1)
        
        self.trans_entries = {}

        ctk.CTkButton(main_frame, text="수정된 내용 저장", command=self.save_translations).grid(row=3, column=0, pady=10)

    def filter_translations(self, event=None):
        for widget in self.trans_scroll_frame.winfo_children():
            widget.destroy()
        self.trans_entries.clear()

        search_term = self.trans_search_entry.get().lower()
        
        if not search_term:
            self.trans_count_label.configure(text="검색어를 입력하여 사전을 편집하세요.")
            return

        full_dict = self.app.translator.custom_dict
        filtered_items = [
            (k, v) for k, v in full_dict.items() 
            if search_term in k.lower() or search_term in v.lower()
        ]

        display_limit = 200 
        displayed_count = 0

        for i, (key, value) in enumerate(filtered_items):
            if displayed_count >= display_limit:
                break
                
            key_entry = ctk.CTkEntry(self.trans_scroll_frame)
            key_entry.insert(0, key)
            key_entry.grid(row=i, column=0, padx=5, pady=2, sticky="ew")

            value_entry = ctk.CTkEntry(self.trans_scroll_frame)
            value_entry.insert(0, value)
            value_entry.grid(row=i, column=1, padx=5, pady=2, sticky="ew")

            self.trans_entries[key] = (key_entry, value_entry)
            displayed_count += 1
        
        if displayed_count < len(filtered_items):
            self.trans_count_label.configure(text=f"{len(filtered_items)}개 중 {displayed_count}개 표시됨. (검색어 구체화 필요)")
        else:
            self.trans_count_label.configure(text=f"{displayed_count}개 항목 표시됨.")

    def save_translations(self):
        for original_key, (key_entry, value_entry) in self.trans_entries.items():
            new_key = key_entry.get()
            new_value = value_entry.get()

            if not new_key:
                if original_key in self.app.translator.custom_dict:
                     del self.app.translator.custom_dict[original_key]
                continue
            
            if original_key != new_key:
                if original_key in self.app.translator.custom_dict:
                    del self.app.translator.custom_dict[original_key]
            
            self.app.translator.custom_dict[new_key] = new_value

        self.app.translator.save_custom_translations()
        
        messagebox.showinfo("저장 완료", "번역 사전이 저장되었습니다.")
        self.filter_translations()


class ImagePromptGallery(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.load_config()
        ctk.set_appearance_mode(self.config.get("theme", "System"))
        ctk.set_default_color_theme("blue")
        setup_directories()
        self.db = DatabaseManager(DB_FILE)
        self.title("프롬프트 이미지 갤러리"); self.geometry("1600x1000")
        self.all_image_files, self.displayed_image_files, self.selected_files = [], [], set()
        self.current_view_mode, self.current_view_id, self.search_term, self.detail_win = "All Images", None, "", None
        self.search_job, self.is_selection_mode = None, False
        self.grid_rowconfigure(1, weight=1); self.grid_columnconfigure(1, weight=1)
        self.create_top_bar(); self.create_tag_sidebar()
        self.scrollable_frame = ctk.CTkScrollableFrame(self); self.scrollable_frame.grid(row=1, column=1, padx=10, pady=10, sticky="nsew")
        self.create_batch_action_bar()
        self.status_label = ctk.CTkLabel(self, text="준비 완료", anchor="w"); self.status_label.grid(row=3, column=0, columnspan=2, padx=10, pady=(0, 5), sticky="ew")
        self.translator = TranslatorService(self)
        self.after(100, self.initial_load)
    def load_config(self):
        try:
            with open(CONFIG_FILE, 'r') as f: self.config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.config = {"image_folder": "images", "thumbnail_width": 180, "thumbnail_height": 240, "theme": "System", "translation_engine": "Hybrid", "filtered_tags": []}
    def create_top_bar(self):
        top_frame = ctk.CTkFrame(self, fg_color="transparent"); top_frame.grid(row=0, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
        top_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top_frame, text="프롬프트 갤러리", font=ctk.CTkFont(size=20, weight="bold")).grid(row=0, column=0, padx=(0, 20))
        search_entry = ctk.CTkEntry(top_frame, placeholder_text="파일 이름, 프롬프트, 태그로 검색..."); search_entry.grid(row=0, column=1, sticky="ew")
        search_entry.bind("<KeyRelease>", self.on_search)
        self.view_mode_button = ctk.CTkButton(top_frame, text="All Images", command=self.open_view_menu)
        self.view_mode_button.grid(row=0, column=2, padx=10)
        admin_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        admin_frame.grid(row=0, column=3, padx=(10,0))
        self.selection_mode_button = ctk.CTkButton(admin_frame, text="선택", command=self.toggle_selection_mode);
        self.selection_mode_button.pack(side="left")
        ctk.CTkButton(admin_frame, text="관리", width=80, command=self.open_management_window).pack(side="left", padx=5)
        ctk.CTkButton(admin_frame, text="설정", width=80, command=self.open_settings).pack(side="left")
        ctk.CTkButton(admin_frame, text="새로고침", width=100, command=self.initial_load).pack(side="left", padx=5)
    def create_batch_action_bar(self):
        self.batch_action_bar = ctk.CTkFrame(self, height=50)
        ctk.CTkButton(self.batch_action_bar, text="선택 해제", command=self.clear_selection).pack(side="left", padx=10)
        
        batch_fav_frame = ctk.CTkFrame(self.batch_action_bar, fg_color="transparent")
        batch_fav_frame.pack(side="left", padx=10)
        ctk.CTkButton(batch_fav_frame, text="즐겨찾기 추가", command=lambda: self.batch_set_favorite(True)).pack(side="left", padx=5)
        ctk.CTkButton(batch_fav_frame, text="즐겨찾기 제거", command=lambda: self.batch_set_favorite(False)).pack(side="left")

        batch_tag_frame = ctk.CTkFrame(self.batch_action_bar, fg_color="transparent")
        batch_tag_frame.pack(side="left", padx=10)
        ctk.CTkButton(batch_tag_frame, text="태그 일괄 추가", command=self.batch_add_tags).pack(side="left", padx=5)
        ctk.CTkButton(batch_tag_frame, text="태그 일괄 제거", command=self.batch_remove_tags).pack(side="left")

        self.selected_count_label = ctk.CTkLabel(self.batch_action_bar, text="0개 선택됨")
        self.selected_count_label.pack(side="right", padx=10)
    def create_tag_sidebar(self):
        self.tag_sidebar = ctk.CTkScrollableFrame(self, label_text="태그 목록", width=200)
        self.tag_sidebar.grid(row=1, column=0, padx=(10,0), pady=10, sticky="ns")
    def update_tag_sidebar(self):
        for widget in self.tag_sidebar.winfo_children(): widget.destroy()
        ctk.CTkButton(self.tag_sidebar, text="[ 모든 태그 해제 ]", command=lambda: self.change_view_mode("All Images")).pack(fill="x", pady=2)
        text_color, hover_color = ("gray10", "gray90"), ("gray85", "gray20")
        for tag_id, tag_name in self.db.get_all_tags():
            ctk.CTkButton(self.tag_sidebar, text=f"#{tag_name}", anchor="w", fg_color="transparent", text_color=text_color, hover_color=hover_color, command=lambda tid=tag_id, tname=tag_name: self.change_view_mode(f"Tag: {tname}", tid)).pack(fill="x", pady=1)
    def update_view_mode_menu(self):
        self.album_map = {f"Album: {name}": aid for aid, name in self.db.get_albums()}
        if self.current_view_mode.startswith("Album:") and self.current_view_mode not in self.album_map:
            self.change_view_mode("All Images")
    def initial_load(self):
        self.load_config()
        self.image_folder = self.config.get("image_folder", "images")
        self.thumbnail_size = (self.config.get("thumbnail_width", 180), self.config.get("thumbnail_height", 240))
        self.status_label.configure(text="이미지 파일 동기화 중..."); self.update()
        if not os.path.isdir(self.image_folder): os.makedirs(self.image_folder)
        file_paths = sorted([os.path.join(self.image_folder, f) for f in os.listdir(self.image_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))], key=os.path.getmtime, reverse=True)
        self.db.sync_files(file_paths)
        self.all_image_files = self.db.get_all_image_paths()
        self.status_label.configure(text="메타데이터 캐싱 중..."); self.update()
        threading.Thread(target=self.update_metadata_cache_threaded, daemon=True).start()
    def filter_and_display_images(self):
        if self.current_view_mode == "Similar Images":
             return
        
        all_files = set(self.all_image_files)
        filtered_tags = self.config.get("filtered_tags", [])
        if filtered_tags:
            blacklisted_paths = self.db.get_image_paths_with_tags(filtered_tags)
            files_to_show = all_files - blacklisted_paths
        else:
            files_to_show = all_files
        mode = self.current_view_mode
        if mode == "Favorites":
            files_to_show &= self.db.get_favorites()
        elif mode.startswith("Album:"):
            files_to_show &= self.db.get_album_images(self.current_view_id)
        elif mode.startswith("Tag:"):
            files_to_show &= self.db.get_images_by_tag(self.current_view_id)
        if self.search_term:
            search_results, term = set(), self.search_term.lower()
            for path in files_to_show:
                pos, neg = self.db.get_parsed_prompts(path)
                tags = " ".join([t[1] for t in self.db.get_image_tags(path)])
                if term in f"{os.path.basename(path).lower()} {pos.lower()} {neg.lower()} {tags.lower()}":
                    search_results.add(path)
            files_to_show &= search_results
        self.displayed_image_files = sorted(list(files_to_show), key=lambda p: self.all_image_files.index(p) if p in self.all_image_files else -1)
        self.populate_gallery()
    def populate_gallery(self):
        for widget in self.scrollable_frame.winfo_children(): widget.destroy()
        columns = max(1, self.scrollable_frame.winfo_width() // (self.thumbnail_size[0] + 40)); self.scrollable_frame.grid_columnconfigure(list(range(columns)), weight=1)
        for i, file_path in enumerate(self.displayed_image_files):
            row, col = i // columns, i % columns
            item_frame = ctk.CTkFrame(self.scrollable_frame); item_frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
            try:
                thumbnail_path = self.get_or_create_thumbnail(file_path)
                if thumbnail_path:
                    ctk_img = ctk.CTkImage(Image.open(thumbnail_path), size=self.thumbnail_size)
                    img_button = ctk.CTkButton(item_frame, image=ctk_img, text="", fg_color="transparent", command=lambda fp=file_path: self.on_thumbnail_click(fp)); img_button.pack(padx=5, pady=5)
                    if not self.is_selection_mode:
                        is_fav, _ = self.db.get_image_data(file_path)
                        fav_button = ctk.CTkButton(item_frame, text="★" if is_fav else "☆", width=28, height=28, command=lambda fp=file_path: self.toggle_favorite(fp)); fav_button.place(in_=img_button, relx=1.0, rely=0.0, anchor="ne", x=-5, y=5)
                        img_button.bind("<Button-3>", lambda event, fp=file_path: self.show_context_menu(event, fp))
                    else:
                        checkbox_var = tk.BooleanVar(value=file_path in self.selected_files)
                        checkbox = ctk.CTkCheckBox(item_frame, text="", variable=checkbox_var, command=lambda fp=file_path, v=checkbox_var: self.on_checkbox_toggle(fp, v)); checkbox.place(in_=img_button, relx=0.0, rely=0.0, anchor="nw", x=5, y=5)
            except Exception as e: print(e)
    def on_thumbnail_click(self, file_path):
        if self.is_selection_mode: self.toggle_selection(file_path)
        else: self.open_detail_view(file_path)
    def on_checkbox_toggle(self, file_path, var):
        if var.get(): self.selected_files.add(file_path)
        else: self.selected_files.discard(file_path)
        self.update_batch_action_bar()
    def toggle_selection(self, file_path):
        checkbox_var = tk.BooleanVar(value=file_path not in self.selected_files); self.on_checkbox_toggle(file_path, checkbox_var); self.populate_gallery()
    def clear_selection(self):
        self.selected_files.clear(); self.update_batch_action_bar(); self.populate_gallery()
    def batch_set_favorite(self, is_fav):
        if not self.selected_files: return
        for path in self.selected_files: self.db.set_favorite(path, is_fav)
        messagebox.showinfo("완료", f"{len(self.selected_files)}개 이미지를 즐겨찾기 {'추가' if is_fav else '제거'}했습니다.")
        self.populate_gallery()
    def batch_add_tags(self):
        if not self.selected_files: return
        dialog = ctk.CTkInputDialog(text="추가할 태그를 입력하세요 (쉼표로 구분):", title="태그 일괄 추가")
        tags_str = dialog.get_input()
        if not tags_str: return
        
        tags_to_add = [t.strip().lower() for t in tags_str.split(',') if t.strip()]
        for path in self.selected_files:
            for tag in tags_to_add:
                self.db.add_tag_to_image(path, tag)
        
        self.update_tag_sidebar()
        messagebox.showinfo("완료", f"{len(self.selected_files)}개 이미지에 태그를 추가했습니다.")

    def batch_remove_tags(self):
        if not self.selected_files: return
        dialog = ctk.CTkInputDialog(text="제거할 태그를 입력하세요 (쉼표로 구분):", title="태그 일괄 제거")
        tags_str = dialog.get_input()
        if not tags_str: return
        
        tags_to_remove = [t.strip().lower() for t in tags_str.split(',') if t.strip()]
        tag_ids_to_remove = []
        for tag_name in tags_to_remove:
            tag_id = self.db.get_tag_id_by_name(tag_name)
            if tag_id:
                tag_ids_to_remove.append(tag_id)
        
        if not tag_ids_to_remove:
            messagebox.showinfo("알림", "존재하지 않는 태그입니다.")
            return

        for path in self.selected_files:
            for tag_id in tag_ids_to_remove:
                self.db.remove_tag_from_image(path, tag_id)
        
        self.update_tag_sidebar()
        messagebox.showinfo("완료", f"{len(self.selected_files)}개 이미지에서 태그를 제거했습니다.")

    def update_batch_action_bar(self):
        count = len(self.selected_files); self.selected_count_label.configure(text=f"{count}개 선택됨")
    def toggle_selection_mode(self):
        self.is_selection_mode = not self.is_selection_mode
        if self.is_selection_mode:
            self.selection_mode_button.configure(fg_color=("lightblue", "blue")); self.batch_action_bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        else:
            self.selection_mode_button.configure(fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"]); self.batch_action_bar.grid_remove(); self.clear_selection()
        self.populate_gallery()
    def show_context_menu(self, event, file_path):
        context_menu = tk.Menu(self, tearoff=0)
        context_menu.add_command(label="유사 이미지 찾기", command=lambda: self.find_similar_images(file_path))
        context_menu.add_separator()
        add_to_album_menu = tk.Menu(context_menu, tearoff=0)
        albums = self.db.get_albums()
        if not albums: add_to_album_menu.add_command(label="(앨범 없음)", state="disabled")
        else:
            for album_id, album_name in albums: add_to_album_menu.add_command(label=album_name, command=lambda aid=album_id: self.db.add_image_to_album(aid, file_path))
        context_menu.add_cascade(label="앨범에 추가", menu=add_to_album_menu); context_menu.add_command(label="새 앨범 만들기...", command=lambda: self.create_new_album_and_add(file_path))
        if self.current_view_mode.startswith("Album:"): context_menu.add_separator(); context_menu.add_command(label=f"'{self.current_view_mode.split(': ')[1]}' 앨범에서 제거", command=lambda: self.remove_image_from_current_album(file_path))
        context_menu.tk_popup(event.x_root, event.y_root)
    def on_search(self, event):
        if self.search_job: self.after_cancel(self.search_job)
        self.search_term = event.widget.get()
        if self.current_view_mode != "All Images":
             self.change_view_mode("All Images")
        self.search_job = self.after(400, self.filter_and_display_images)
    def change_view_mode(self, mode, view_id=None):
        if mode.startswith("Album:"): self.current_view_id = self.album_map.get(mode)
        else: self.current_view_id = view_id
        
        self.current_view_mode = mode
        short_name = mode.split(': ')[-1]
        self.view_mode_button.configure(text=short_name if len(short_name) < 20 else short_name[:17] + "...")
        
        self.search_term = ""
        self.filter_and_display_images()

    def find_similar_images(self, source_path):
        self.status_label.configure(text=f"'{os.path.basename(source_path)}'와(과) 유사한 이미지 검색 중...")
        self.update_idletasks()

        source_prompt, _ = self.db.get_parsed_prompts(source_path)
        if not source_prompt:
            messagebox.showinfo("알림", "기준 이미지의 프롬프트 정보가 없습니다.")
            self.status_label.configure(text="준비 완료")
            return
        
        source_tags = {tag.strip().lower() for tag in source_prompt.split(',') if tag.strip()}
        
        scores = []
        for path in self.all_image_files:
            if path == source_path: continue
            
            other_prompt, _ = self.db.get_parsed_prompts(path)
            if not other_prompt: continue
            
            other_tags = {tag.strip().lower() for tag in other_prompt.split(',') if tag.strip()}
            
            score = len(source_tags.intersection(other_tags))
            if score > 0:
                scores.append((score, path))
        
        scores.sort(key=lambda x: x[0], reverse=True)
        
        self.displayed_image_files = [path for score, path in scores]
        self.current_view_mode = "Similar Images"
        self.view_mode_button.configure(text="유사 이미지 검색 결과")
        self.populate_gallery()
        self.status_label.configure(text=f"유사 이미지 {len(self.displayed_image_files)}개 검색 완료.")

    def toggle_favorite(self, file_path):
        is_fav = not (self.db.get_image_data(file_path)[0] == 1); self.db.set_favorite(file_path, is_fav); self.populate_gallery()
    def create_new_album_and_add(self, file_path):
        dialog = ctk.CTkInputDialog(text="새 앨범 이름을 입력하세요:", title="앨범 만들기"); album_name = dialog.get_input()
        if album_name: self.db.add_album(album_name); album_id = self.db._execute("SELECT id FROM albums WHERE name=?", (album_name,), fetch='one')[0]; self.db.add_image_to_album(album_id, file_path); self.update_view_mode_menu()
    def remove_image_from_current_album(self, file_path):
        self.db.remove_image_from_album(self.current_view_id, file_path); self.filter_and_display_images()
    def get_or_create_thumbnail(self, fp):
        bn = os.path.basename(fp); tp = os.path.join(THUMBNAIL_DIR, bn)
        if not os.path.exists(tp) or os.path.getmtime(fp) > os.path.getmtime(tp):
            try:
                with Image.open(fp) as img: img.thumbnail((512, 512), Image.Resampling.LANCZOS); img.save(tp, "PNG")
            except Exception as e: print(f"Error creating thumbnail for {fp}: {e}"); return None
        return tp
    def update_metadata_cache_threaded(self):
        for path in self.all_image_files:
            _, timestamp = self.db.get_image_data(path)
            if not timestamp or os.path.getmtime(path) > timestamp:
                try:
                    with Image.open(path) as img: parsed_data = parse_image_metadata(img.info)
                    self.db.update_image_cache(path, parsed_data, os.path.getmtime(path))
                except Exception: self.db.update_image_cache(path, {'prompt':'', 'negative_prompt':'', 'others':''}, os.path.getmtime(path))
        self.after(0, self.update_after_cache)
    def update_after_cache(self):
        self.status_label.configure(text=f"{len(self.all_image_files)}개 이미지 로드 완료. 검색 준비 완료."); self.update_tag_sidebar(); self.filter_and_display_images()
    def open_detail_view(self, file_path):
        if self.detail_win is not None and self.detail_win.winfo_exists(): self.detail_win.destroy()
        self.detail_win = DetailWindow(self, file_path); self.detail_win.focus()
    def open_settings(self):
        SettingsWindow(self)
    def open_management_window(self):
        ManagementWindow(self)
    def restart_program(self):
        self.destroy(); os.execl(sys.executable, sys.executable, *sys.argv)
    def open_view_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="All Images", command=lambda: self.change_view_mode("All Images"))
        menu.add_command(label="Favorites", command=lambda: self.change_view_mode("Favorites"))
        albums = self.db.get_albums()
        if albums:
            menu.add_separator()
            for album_id, album_name in albums:
                menu.add_command(label=f"앨범: {album_name}", command=lambda mode=f"Album: {album_name}", aid=album_id: self.change_view_mode(mode, aid))
        x = self.view_mode_button.winfo_rootx()
        y = self.view_mode_button.winfo_rooty() + self.view_mode_button.winfo_height()
        menu.tk_popup(x, y)

if __name__ == "__main__":
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
    app = ImagePromptGallery()
    app.mainloop()