from enum import StrEnum
from dataclasses import asdict
import json

from src.models import *

spk_symbol = "🧑"

spknum_illegible = "/999999" # номер зарезервирован для..
spk_illegible_title = "It's completely garbled" # Всё совершенно неразборчиво

class TextFormat(StrEnum):
    subtitle  = "srt"
    text      = "txt"
    table_csv = "csv"
    table_tsv = "tsv"
    json      = "json"

def get_subtitle_time_str(pos: int) -> str:
    total_seconds = pos // 1000
    milliseconds = pos % 1000
    
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    # Собираем строку с ведущими нулями
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"    

def result_get_text(text_format: TextFormat, result_list: List[ResultSegment], spknmap: dict[str, str])->str:
    text: str = ""
    spk_name: str = "-"

    if text_format == TextFormat.text:
        for item in result_list:
            item_spk_name = spknmap[item.spkid]

            if spk_name != item_spk_name:
                spk_name = item_spk_name
                text += f"\n {spk_symbol} {spk_name}"

            text += f"\n{item.text}"
    
    if text_format == TextFormat.subtitle:
        for index, item in enumerate(result_list, start=1):
            text += f"\n{index}"
            text += f"\n{get_subtitle_time_str(item.tss)} --> {get_subtitle_time_str(item.tse)}"

            item_spk_name = spknmap[item.spkid].replace(" ", "_")

            if spk_name != item_spk_name:
                spk_name = item_spk_name
                text += f"\n{spk_symbol}{spk_name} {item.text}"
            else:
                text += f"\n{item.text}"

            text += f"\n"

    if text_format == TextFormat.table_csv:
        for index, item in enumerate(result_list, start=1):
            text += f"\n{index},{item.spkid},{item.tss},{item.tse},{item.text.replace(',', ' ')}"

    if text_format == TextFormat.table_tsv:
        for index, item in enumerate(result_list, start=1):
            text += f"\n{index}\t{item.spkid}\t{item.tss}\t{item.tse}\t{item.text}"
            
    if text_format == TextFormat.json:
        dicts = [asdict(result) for result in result_list]
        text = json.dumps(dicts, indent=4, ensure_ascii=False)

    return text

def prepare_speakers(results: List[ResultSegment], spks: list[Speaker], spkimap: dict[str, Speaker], spknmap: dict[str, str]):
    for spk in spks:
        spkimap[spk.spkid] = spk

    for result in results:
        if not spkimap.get(result.spkid):
            spkimap[result.spkid] = Speaker(
                spkid=result.spkid,
                title=spk_illegible_title if result.spkid.endswith(spknum_illegible) else result.spkid,
                ref_spkid="",
                is_good=False,
                is_bad=True
            )    

    spknmap.clear()
    spks_values = spkimap.values()

    for spk in spks_values:
        if spk.title != "" and spk.ref_spkid != "" and spk.ref_spkid != spk.spkid:
            title= spknmap.get(spk.ref_spkid)
            if title is None:
                spknmap[spk.ref_spkid] = spk.title
            elif spk.title != title:
                spknmap[spk.ref_spkid] = "*"

    for spk in spks_values:
        if spk.title != "":                
            spknmap[spk.spkid] = spk.title

    for spk in spks_values:
        if spk.title == "" and spk.ref_spkid != "" and spk.ref_spkid != spk.spkid:
            title= spknmap.get(spk.ref_spkid)
            if title is not None:
                spknmap[spk.spkid] = title

    for spk in spks_values:
        if spk.spkid not in spknmap:
            spknmap[spk.spkid] = spk.spkid

    for spkid, title in spknmap.items():
        if title == "*":
            spknmap[spkid] = spkid
    
    return spknmap