import soundfile as sf
import numpy as np
from typing import Any, List, Tuple, Optional
from dataclasses import asdict
from collections import defaultdict
from pathlib import Path

from listelog_pyworker_lib.file_processor import *
from listelog_pyworker_lib.models import *

from src.audio_emb_extractor_redimnet2 import *
from src.vad import *

def _get_audio_file_content(audio_file_path: str | Path) -> np.ndarray:
    """Читает весь контент аудиофайла ogg opus и возвращает массив сэмплов float32."""
    data: np.ndarray
    sample_rate: int
    
    data, sample_rate = sf.read(audio_file_path, dtype="float32")
    return data

@dataclass(frozen=True)
class _IntSegment:
    start: int   # позиция сэмпла начала
    end:   int   # позиция сэмпла конца 
    spkid: str   # ИД спикера

@dataclass(frozen=True)
class _MossSpeaker:
    mspkid: str # moss ИД спикера сегмета 
    emb: np.ndarray # эмбеддинг
    is_good: bool # эмбединг получен от достаточно длинного сегмента

@dataclass(frozen=True)
class _Pause:
    left:  int # позиция сэмпла с лева
    right: int # позиция сэмпла с права

@dataclass(frozen=True)
class _SpeakerValue:
    is_good: bool
    is_bad: bool
    ref_spknum: int

@dataclass(frozen=True)
class _SpkembConfig(AbstractConfig):
    silero_vad_model_path: str
    wespeaker_model_path: str
    wespeaker_threshold: float
    wespeaker_device: str  
    max_duration_kf: float # Коэффицент для расчёта максимальной длительности обраотки
    max_duration_add: float # Добавка для расчёта максимальной длительности обраотки

    def __init__(self, config_path: str) -> None:
        super().__init__(config_path)  
        
class SpkembProcessorMoss(FileProcessor):
    def __init__(self, main_config: MainConfig, config_path:str) -> None:
        self._main_config = main_config
        self._config = _SpkembConfig(config_path)
        self.work_types = "spkemb"
        self.next_work_type = ""        

        self.sample_rate: int = 16000
        self.good_duration: float = 3.0 # сек. длительность сегмента для получения каественного эбеддинга

        self.extractor = AudioEmbeddingExtractor(self._config.wespeaker_model_path, self._config.wespeaker_device)
        self.vad = VADProcessor(self._config.silero_vad_model_path)

        self.speakers: List[SessionSpeaker] = []
        self.last_spknum: int = 0
        self.spkorder: List[int] = [] # Последовательность выступления спикеров, содержит spknum

    def get_work_types(self) -> str:
        return self.work_types

    async def process_file(self, task: Task) -> str:
        self._init_file(task.work_type, task.sid, task.cid)
        self._load_session()

        audio_data = _get_audio_file_content(task.original_file)

        max_proc_duration_sec = (len(audio_data) / self.sample_rate) * self._config.max_duration_kf + self._config.max_duration_add
        task.start_processing(max_proc_duration_sec)

        result, spks = self._process_spkemb(audio_data)

        self._save_session()

        task.save_sttd_result(result, spks) 

        return self.next_work_type

    def _build_spkid(self, spknum: int) -> str:
        return f"{self.chunk_info.src}/{spknum}" if spknum >= 0 else ""

    def _calc_speaker_quality(self, speaker: SessionSpeaker)->None:
        speaker.is_good = speaker.title != "" or speaker.good_count >= 2 
        speaker.is_bad  = speaker.max_seg_duration < 1.5

    def _process_spkemb(self, audio_data: np.ndarray) -> Tuple[list[RecognitionResult], List[RecognitiontSpeaker]]:        
        segments = self._load_sttd_segments()  
        spkembs = self._get_speaker_emb(audio_data, segments)

        # сохраняем текущее состояние спикеров
        prespk: dict[int, _SpeakerValue] = {} # spknum, ref_spknum
        for speaker in self.speakers:
            if speaker.spknum != -1:
                prespk[speaker.spknum] = _SpeakerValue(speaker.is_good, speaker.is_bad, speaker.ref_spknum)
    
        mspkmap: dict[str, int] = {}
        spkindex: dict[int, SessionSpeaker] = {}

        for mosspk in spkembs:
            best_spk, best_index, best_score = self._best_match_speakers(mosspk.emb, self.speakers)

            if best_spk is not None and best_score >= self._config.wespeaker_threshold:
                spk = best_spk
                del self.speakers[best_index]
            else:
                spk = SessionSpeaker(
                    emb=mosspk.emb, 
                )

            self.speakers.insert(0, spk)

            if spk.spknum == -1:
                self.last_spknum += 1
                spk.spknum = self.last_spknum

            mspkmap[mosspk.mspkid] = spk.spknum
            spkindex[spk.spknum] = spk

        ses_seg_index: int = len(self.spkorder)
        prev_spknum: int = -1
        prev_spk: SessionSpeaker | None = None
        spk_block_duration: float = 0.0

        result: List[RecognitionResult] = []       
        for segment in segments:
            spknum = mspkmap[segment.speaker]

            self.spkorder.append(spknum)
            ses_seg_index += 1

            spk = spkindex[spknum]

            seg_duration = segment.end - segment.start
            
            if spknum != prev_spknum:
                if prev_spk:
                    if spk_block_duration >= self.good_duration:
                        prev_spk.good_count += 1
                        self._calc_speaker_quality(prev_spk)

                spk_block_duration = 0
                spk.count += 1
                prev_spknum = spknum
                prev_spk = spk

            spk_block_duration += seg_duration
            spk.duration += seg_duration

            if spk.max_seg_duration < seg_duration:
                spk.max_seg_duration = seg_duration


            if spk.first_ssegi == -1:
                spk.first_ssegi = ses_seg_index

            result.append(RecognitionResult(
                tss   = self.chunk_info.tss + round(segment.start * 1000),
                tse   = self.chunk_info.tss + round(segment.end * 1000),
                spkid = self._build_spkid(spknum),
                text  = segment.text
            ))

        if prev_spk: # Обрабатываем результаты последнего сегмента
            if spk_block_duration >= self.good_duration:
                prev_spk.good_count += 1
                self._calc_speaker_quality(prev_spk)

        self._merge_speakers(self.speakers, self.spkorder)

        # заполняем список добавленых/изменённых спикеров
        spks: List[RecognitiontSpeaker] = []
        for speaker in self.speakers:
            if speaker.spknum == -1: continue

            spkv = _SpeakerValue(speaker.is_good, speaker.is_bad, speaker.ref_spknum)

            if speaker.spknum in prespk and prespk[speaker.spknum] == spkv:
                continue

            spks.append(RecognitiontSpeaker(
                spkid     = self._build_spkid(speaker.spknum),
                title     = speaker.title,
                ref_spkid = self._build_spkid(speaker.ref_spknum),
                is_good   = speaker.is_good,
                is_bad    = speaker.is_bad
            ))

        return result, spks

    def _load_sttd_segments(self) -> List[SttdSegment]:
        with open(self.sttd_path, "r", encoding="utf-8") as f:
            sttd_segments: list[dict[str, Any]] = json.load(f)

        result: List[SttdSegment] = []

        for item in sttd_segments:
            result.append(
                SttdSegment(
                    start   = item["start"],
                    end     = item["end"],
                    speaker = item["speaker"],
                    text    = item["text"],
                )
            )

        return result

    def _get_speaker_emb(self, audio_data: np.ndarray, segments: List[SttdSegment]) -> List[_MossSpeaker]: 
        if not segments:
            return []
            
        # Объединение последовательно идущих сегментов одного спикера
        merged_segments: List[SttdSegment] = []
        current_seg = segments[0]

        for next_seg in segments[1:]:
            if next_seg.speaker == current_seg.speaker:
                # Объединяем: берем старт первого и энд второго
                current_seg = SttdSegment(
                    start=current_seg.start,
                    end=next_seg.end,
                    speaker=current_seg.speaker,
                    text=current_seg.text + " " + next_seg.text
                )
            else:
                merged_segments.append(current_seg)
                current_seg = next_seg
        merged_segments.append(current_seg)

        # Корректирвка границ сегмента по паузам
        pauses: dict[int, _Pause] = {}
        def pause(pos: int) -> _Pause:
            pause_pos = pauses.get(pos)
            if pause_pos is not None:
                return pause_pos
            
            pause_pos = self._get_near_pause(audio_data, self.sample_rate, pos, 350)
            pauses[pos] = pause_pos
            return pause_pos

        int_segments: List[_IntSegment] = []

        audio_len = len(audio_data)
        
        for seg in merged_segments:
            # Игнорируем сегмент, если он < 1.5 сек И есть сегменты этогоже спикера >= 1.5 сек
            if (seg.end - seg.start < 1.5 
            and any(xseg.end - xseg.start >= 1.5 for xseg in merged_segments if xseg.speaker == seg.speaker)):
                continue
                
            # Извлекаем эмбеддинг для прошедшего фильтрацию сегмента
            seg_start_orig = min(int(round(seg.start * self.sample_rate)), audio_len) 
            seg_end_orig   = min(int(round(seg.end * self.sample_rate)), audio_len)
            seg_start = pause(seg_start_orig).right                
            seg_end   = pause(seg_end_orig).left

            if seg_start >= seg_end:
                seg_start = seg_start_orig
                seg_end   = seg_end_orig

            int_segments.append(_IntSegment(seg_start, seg_end, seg.speaker))

        return self._get_speaker_emb_int(audio_data, int_segments)

    def _get_speaker_emb_int(self, audio_data: np.ndarray, segments: List[_IntSegment]) -> List[_MossSpeaker]:
        SAMPLE_RATE = 16000
        MAX_LEN = 6 * SAMPLE_RATE

        good_len = self.good_duration * SAMPLE_RATE
        good_spk: List[str] = []

        
        # 1. Нарезка и паддинг сегментов
        grouped_chunks = defaultdict(list)
        
        for seg in segments:
            length = seg.end - seg.start
            if length <= 0:
                continue

            if length > good_len and not seg.spkid in good_spk:
                good_spk.append(seg.spkid)
                
            if length > MAX_LEN:
                for start_idx in range(seg.start, seg.end, MAX_LEN):
                    end_idx = start_idx + MAX_LEN
                    if end_idx <= seg.end:
                        chunk = audio_data[start_idx:end_idx]
                    else:
                        chunk = audio_data[max(0, seg.end - MAX_LEN) : seg.end]
                        # На случай, если весь сегмент или файл короче 6 секунд, 
                        # но логика выше гарантирует length > MAX_LEN, так что max(0, ...) для надежности
                    grouped_chunks[MAX_LEN].append((seg.spkid, chunk))
            else:
                #seconds = int(np.ceil(length / SAMPLE_RATE))
                seconds = 6 # тестируем выравнивание всего до 6 сек
                pad_len = seconds * SAMPLE_RATE
                
                chunk = audio_data[seg.start : seg.end]
                if length < pad_len:
                    # chunk = np.pad(chunk, (0, pad_len - length), mode='constant')

                    # Сколько сэмплов нужно добавить справа
                    needed_samples = pad_len - length
                    
                    # Используем mode='reflect' для зеркального дублирования аудио.
                    # Если исходный фрагмент слишком короткий (меньше половины нужной длины),
                    # reflect может выбросить ошибку. На этот случай добавлен режим 'edge' как фолбек.
                    if length > 1:
                        chunk = np.pad(chunk, (0, needed_samples), mode='reflect')
                    else:
                        chunk = np.pad(chunk, (0, needed_samples), mode='edge')                    
                    
                grouped_chunks[pad_len].append((seg.spkid, chunk))
                
        # 2. Пакетная обработка через get_embeddings
        speaker_embs_collected = defaultdict(list)
        
        for pad_len, chunks_info in grouped_chunks.items():
            if not chunks_info:
                continue
            
            batch_buffers = [chunk for _, chunk in chunks_info]
            embeddings = self.extractor.get_embeddings(batch_buffers)
            
            for (spkid, _), emb in zip(chunks_info, embeddings):
                speaker_embs_collected[spkid].append(emb)
                
        # 3. Расчет среднего эмбеддинга для каждого спикера и L2-нормализация
        result = []
        for spkid, embs in speaker_embs_collected.items():
            mean_emb = np.mean(embs, axis=0)
            
            # Применяем L2-нормализацию к усредненному вектору
            norm = np.linalg.norm(mean_emb)
            if norm > 0:
                mean_emb = mean_emb / norm
                
            result.append(_MossSpeaker(mspkid=spkid, emb=mean_emb, is_good = spkid in good_spk ))
            
        return result
    

    def _init_file(self, work_type: str, sid: str, cid: str) -> None:
        self.work_type = work_type
        self.sid = sid
        self.cid = int(cid)

        self.session_dir = Path(self._main_config.sessions_dir) / self.sid     

        session_head_path: str = str(self.session_dir / f"session_head.json")
        with open(session_head_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            session_head = SessionHead(**data)  

        self.chunk_info_path: str = str(self.session_dir / "info" / f"{self.cid}_info.json")

        with open(self.chunk_info_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.chunk_info = ChunkInfo(**data)        

        user_dir: Path = Path(self._main_config.users_dir) / session_head.user
        self.usrspk_path = str(user_dir / f"speakers.json")

        self.sttd_path   = str(self.session_dir / "sttd" / f"{self.cid}_sttd.json")

        self.sesspk_path = str(self.session_dir / f"condidats_{self.chunk_info.src}.json")

    def _load_session(self) -> None:
        self.speakers.clear()
        self.spkorder.clear()
        
        if Path(self.sesspk_path).exists():
            # with open(self.sesspk_path, 'rb') as f:
            #     return pickle.load(f)

            with open(self.sesspk_path, "r", encoding="utf-8") as f:
                file_data = json.load(f)
                ses_speakers: list[dict[str, Any]] = file_data["speakers"]
                ses_spkorder: list[int] = file_data["spkorder"]

            for item in ses_speakers:
                self.speakers.append(
                    SessionSpeaker(
                        emb              = np.array(item["emb"]),
                        spknum           = item["spknum"],
                        title            = item["title"],
                        count            = item["count"],
                        duration         = item["duration"],
                        max_seg_duration = item["max_seg_duration"],
                        good_count       = item["good_count"],
                        is_good          = item["is_good"],
                        is_bad           = item["is_bad"],
                        first_ssegi      = item["first_ssegi"],
                        ref_spknum       = item["ref_spknum"]
                    )                    
                )

            self.last_spknum: int = 0
            for speaker in self.speakers:
                if speaker.spknum != -1:
                    self.last_spknum += 1

            self.spkorder.extend(ses_spkorder)
               
            return

        if Path(self.usrspk_path).exists():
            with open(self.usrspk_path, "r", encoding="utf-8") as f:
                user_peakers: list[dict[str, Any]] = json.load(f)

            for item in user_peakers:
                self.speakers.append(
                    SessionSpeaker(
                        emb              = np.array(item["emb"]), 
                        title            = item["title"],
                        is_good          = True
                    )
                )

    def _save_session(self) -> None:
        # with open(self.sesspk_save_path, 'wb') as f:
        #     # pickle.HIGHEST_PROTOCOL обеспечивает максимальную скорость и сжатие бинарных данных
        #     pickle.dump(speakers, f, protocol=pickle.HIGHEST_PROTOCOL)

        data_to_save: dict[str, Any] = {
            "speakers" : [asdict(s) for s in self.speakers],
            "spkorder" : self.spkorder
        }

        with open(self.sesspk_path, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, default=self._json_encoder, ensure_ascii=False, indent=4)  

    def _json_encoder(self, obj: Any) -> Any:
        """
        Преобразует np.ndarray в обычный список python
        """
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")            

    def _best_match_speakers(self, emb: np.ndarray, speakers: list[SessionSpeaker]
    ) -> Tuple[Optional[SessionSpeaker], int, float]:
        """Return (speaker_id, max_cosine_similarity) for an embedding."""
        best_spk = None
        best_index: int = -1
        best_score = -1.0
        for index, spk in enumerate(speakers):
            score = self._cosine_similarity(emb, spk.emb)
            if score > best_score:
                best_score = score
                best_spk = spk
                best_index = index
        return best_spk, best_index, best_score

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Вычисляет косинусное сходство между дувумя эмбедингами"""
        return float(np.dot(a, b))

    def _get_near_pause(
        self,
        audio_data: np.ndarray,  # аудио-данные 16 кГц, моно, float32
        sample_rate: int,
        pos: int,                # позиция (в сэмплах)
        distance: int            # расстояние поиска в мс (например, 300)
    ) -> _Pause:
        pause = self._get_near_pause_vad(audio_data, sample_rate, pos, distance)
        if pause is not None:
            return pause
                
        pause = self._get_near_pause_rms(audio_data, sample_rate, pos, distance)
        if pause is not None:
            return pause

        return _Pause(pos, pos)

    def _get_near_pause_rms(
        self,
        audio_data: np.ndarray,  # аудио-данные 16 кГц, моно, float32
        sample_rate: int,
        pos: int,                # позиция (в сэмплах)
        distance: int            # расстояние поиска в мс (например, 300)
    ) -> _Pause | None:
        # Переводим миллисекунды в количество сэмплов
        dist_samples = int(distance * sample_rate / 1000)
        win_samples = int(30 * sample_rate / 1000)  # окно 30 мс

        # Определяем границы области поиска с учётом размеров массива
        start_search = max(0, pos - dist_samples)
        end_search = min(len(audio_data), pos + dist_samples)

        # Проверяем, достаточно ли данных для анализа
        if end_search - start_search < win_samples:
            return None

        # Квадраты амплитуд и скользящее RMS (векторно)
        squared = audio_data[start_search:end_search] ** 2
        cumsum = np.cumsum(np.insert(squared, 0, 0))
        win_sums = cumsum[win_samples:] - cumsum[:-win_samples]
        rms_values = np.sqrt(win_sums / win_samples)

        # Порог тишины
        min_rms = np.min(rms_values)
        threshold = min_rms * 1.05 if min_rms > 0 else 1e-5

        # Маска тихих окон
        is_silent = rms_values <= threshold

        # Поиск самой длинной непрерывной последовательности тихих окон
        max_len = 0
        best_start_idx = 0  # индекс первого окна в последовательности (в массиве is_silent)
        current_len = 0
        current_start = 0

        for i, silent in enumerate(is_silent):
            if silent:
                if current_len == 0:
                    current_start = i
                current_len += 1
            else:
                if current_len > max_len:
                    max_len = current_len
                    best_start_idx = current_start
                current_len = 0

        # Проверка, если последовательность доходит до конца
        if current_len > max_len:
            max_len = current_len
            best_start_idx = current_start

        # Если тишина не найдена
        if max_len == 0:
            return None

        # Вычисляем границы паузы в исходном массиве
        # Начало первого тихого окна
        left_sample = start_search + best_start_idx
        # Конец последнего тихого окна (начало + длина окна)
        right_sample = start_search + (best_start_idx + max_len) + win_samples

        # Гарантируем, что правая граница не выходит за пределы
        right_sample = min(right_sample, len(audio_data))

        return _Pause(left=left_sample, right=right_sample)

    def _get_near_pause_vad(
        self,                    # Предполагается, что метод находится в классе, где есть voice_detected
        audio_data: np.ndarray,  # аудио-данные 16 кГц, моно, float32
        sample_rate: int,
        pos: int,                # позиция (в сэмплах)
        distance: int            # расстояние поиска в мс (например, 300)
    ) -> _Pause | None:
        # Инициализация VAD для нового запуска
        self.vad.reset_states() 

        # 1. Вычисляем радиус поиска в сэмплах
        # Используем ceil для гарантированного покрытия временного промежутка
        window_samples = int(np.ceil((distance * sample_rate) / 1000))
        
        # 2. Определяем границы области поиска
        search_start = max(0, pos - window_samples)
        search_end = min(len(audio_data), pos + window_samples)
        
        # Размер одного чанка фиксирован по условию
        chunk_size = 512
        
        # Списки для хранения всех найденных пауз
        pauses = []
        current_pause_start = None
        
        # 3. Сканируем область поиска с шагом в chunk_size
        # Итерируемся только по тем позициям, где можно взять полный чанк
        for i in range(search_start, search_end - chunk_size + 1, chunk_size):
            chunk = audio_data[i : i + chunk_size]
            
            # Проверяем наличие голоса в чанке
            is_voice = self.vad.voice_detected(chunk)
            
            if not is_voice:
                # Если это начало новой паузы
                if current_pause_start == None:
                    current_pause_start = i
            else:
                # Если голос обнаружен и до этого шла пауза — фиксируем её окончание
                if current_pause_start is not None:
                    pauses.append(_Pause(left=current_pause_start, right=i))
                    current_pause_start = None
                    
        # Если область поиска закончилась, а пауза продолжалась
        if current_pause_start is not None:
            pauses.append(_Pause(left=current_pause_start, right=search_end))
            
        # 4. Выбираем самую длинную паузу
        if not pauses:
            return None
            
        # Сортируем по длине (right - left) в убывающем порядке
        longest_pause = max(pauses, key=lambda p: p.right - p.left)
        
        return longest_pause


    def _merge_speakers(
        self,
        sesspks: List[SessionSpeaker],
        spkorder: List[int],
        merge_threshold: float = 0.6,
        assign_threshold: float = 0.6,
        assign_threshold_low: float = 0.5,
        min_reliable_count: int = 2
    ) -> None:
        """
        Объединяет спикеров после завершения записи.
        У основных спикеров ref_spkid остаётся пустым, у вторичных
        заполняется spkid'ом основного спикера.
        """
        if not sesspks:
            return

        reliable: List[SessionSpeaker] = []
        unreliable: List[SessionSpeaker] = []

        # 1. Разделение на надёжных и ненадёжных
        for spk in sesspks:
            if spk.spknum == -1:
                continue

            spk.ref_spknum = -1

            if spk.is_good:
                reliable.append(spk)
            else:
                unreliable.append(spk)

        # Если надёжных нет – кластеризуем всех одинаково (запасной вариант)
        if not reliable:
            reliable = sesspks
            unreliable = []

        reliable_embs = np.array([spk.emb for spk in reliable])

        # 2. Кластеризация надёжных через граф связей по порогу
        n_rel = len(reliable)
        # матрица косинусных сходств
        sim_matrix = np.dot(reliable_embs, reliable_embs.T)
        # граф: ребро, если сходство >= merge_threshold (диагональ исключаем)
        adj = sim_matrix >= merge_threshold
        np.fill_diagonal(adj, False)

        # Поиск компонент связности (простой DFS)
        visited = [False] * n_rel
        clusters = []  # список списков индексов
        for i in range(n_rel):
            if not visited[i]:
                stack = [i]
                comp = []
                while stack:
                    node = stack.pop()
                    if not visited[node]:
                        visited[node] = True
                        comp.append(node)
                        # добавляем соседей
                        neighbors = np.where(adj[node])[0]
                        stack.extend(neighbors)
                clusters.append(comp)

        # 3. Определение основного спикера в каждом кластере
        main_spknum = {}  # spkid -> spkid основного (для себя – свой же)
        for comp in clusters:
            # выбираем по duration, затем по count
            best_idx = max(comp, key=lambda idx: (reliable[idx].duration, reliable[idx].count))
            main_num = reliable[best_idx].spknum
            for idx in comp:
                spk: SessionSpeaker = reliable[idx]
                spk.ref_spknum = main_num
                main_spknum[spk.spknum] = main_num   # маппинг для последнего этапа

        # Если были только надёжные и без ненадёжных – готово
        if not unreliable:
            return

        # 4. Присоединение ненадёжных к ближайшему надёжному
        # Для каждого ненадёжного ищем самого похожего надёжного
        for spk_u in unreliable:
            # сходство со всеми надёжными
            sims = np.dot(reliable_embs, spk_u.emb)  # [n_rel]
            best_rel_idx = int(np.argmax(sims))
            best_sim = sims[best_rel_idx]

            rel_spk = reliable[best_rel_idx]
            main_num = main_spknum[rel_spk.spknum]

            if best_sim >= assign_threshold:
                # Присоединяем к основному того кластера, куда входит best_rel_idx
                spk_u.ref_spknum = main_num
            elif spk_u.count == 1:
                findex = spk_u.first_ssegi - 1 # нумерация с 1
                prev_spknum = spkorder[findex - 1] if spk_u.first_ssegi > 0 else -1
                next_spknum = spkorder[findex + 1] if spk_u.first_ssegi < len(spkorder) else - 1

                if  best_sim >= assign_threshold_low:
                    # если это предыдущий или сдедующий спикер - присваиваем его
                    if prev_spknum == main_num or next_spknum == main_num:
                        spk_u.ref_spknum = main_num
                elif best_sim < assign_threshold_low and spk_u.duration <= 1.0:
                    # с обоих сторон этот спикер
                    if prev_spknum == main_num and next_spknum == main_num:
                        spk_u.ref_spknum = main_num            
                elif best_sim < assign_threshold_low and spk_u.duration <= 0.7:
                    # сегмент короткий и с обоих сторон один и тотже спикер
                    if prev_spknum == next_spknum and prev_spknum in main_spknum:
                        spk_u.ref_spknum = spkorder[spk_u.first_ssegi - 1]  
