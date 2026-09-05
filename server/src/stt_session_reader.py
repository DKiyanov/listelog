import json
import os
from typing import List, Tuple
from datetime import datetime
from pathlib import Path

from src.config import *
from src.models import *

class SttSessionReader:
    def __init__(self, config: Config) -> None:
        self.config = config

    def _load_broken_json_array(self, file_path: str) -> List[dict]:
        """Вспомогательный метод для чтения JSON без внешних скобок []"""
        print(f"json_file_load = {file_path}")
        if not os.path.exists(file_path):
            return []
            
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            
        if not content:
            return []
            
        # Удаляем лишнюю запятую в конце, если она есть
        if content.endswith(','):
            content = content[:-1]
            
        # Оборачиваем в скобки для валидного JSON-массива
        valid_json_str = f"[{content}]"
        return json.loads(valid_json_str)

    def get_user_sessions(self, login: str) -> List[SttSession]:
        file_path = os.path.join(self.config.users_dir, login, 'log.json')
        raw_records = self._load_broken_json_array(file_path)
        
        sessions = []
        for record in raw_records:
            # Фильтруем только события "OPEN"
            if record.get("action") != "OPEN":
                continue
                
            # Переводим дату из ISO формата в YYYYMMDDHHmm
            iso_date = record.get("date_time", "")
            try:
                # Обрезаем таймзону/микросекунды для надежности парсинга
                clean_date = iso_date.split('.')[0]
                dt = datetime.strptime(clean_date, "%Y-%m-%dT%H:%M:%S")
                formatted_date = dt.strftime("%Y%m%d%H%M")
            except ValueError:
                formatted_date = ""

            session = SttSession(
                sid = str(record.get("data", "")),
                title = record.get("title", ""),
                date = formatted_date,
                duration = 0
            )
            sessions.append(session)
            
        return sessions

    def get_session_data(self, sid: str) ->Tuple[List[SttSegment], List[SttSpeaker]] :
        session_dir = Path(self.config.sessions_dir) / sid
        segments_file_path = session_dir / "result.json"
        raw_segments = self._load_broken_json_array(str(segments_file_path))
        
        segments = []
        for segm in raw_segments:
            segments.append(SttSegment(
                cid = int(segm.get("cid", 0)),
                tss = int(segm.get("tss", 0)),
                tse = int(segm.get("tse", 0)),
                spkid = segm.get("spkid", ""),
                text = segm.get("text", "")
            ))

        # Сортируем по значению tss
        segments.sort(key=lambda x: x.tss)

        spks: List[SttSpeaker] = []
        condidats = list(session_dir.glob("condidats_*.json"))
 
        for condidats_file in condidats:
            self._add_condidats(condidats_file, spks)

        return segments, spks

    def _add_condidats(self, condidats_file: Path, spks: List[SttSpeaker] ) -> None:
        src = condidats_file.stem.replace(".json", "").split("_")[1]

        with open(condidats_file, "r", encoding="utf-8") as f:
            file_data = json.load(f)
            ses_speakers: list[dict[str, Any]] = file_data["speakers"]

        for item in ses_speakers:
            spknum = int(item['spknum'])
            if spknum < 0: continue

            ref_spknum = int(item["ref_spknum"])
            ref_spkid = f"{src}/{ref_spknum}" if ref_spknum >= 0 else ""

            spks.append(
                SttSpeaker(
                    spkid     = f"{src}/{spknum}",
                    title     = item["title"],
                    ref_spkid = ref_spkid,
                    is_good   = item["is_good"],
                    is_bad    = item["is_bad"],
                )                    
            )        
