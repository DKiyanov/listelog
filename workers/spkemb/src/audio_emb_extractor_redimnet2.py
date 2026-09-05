import logging

from typing import List

import numpy as np

import torch
import torch.nn.functional as F
import torchaudio
# pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128

import types
import sys

# для работы wespeaker - делаем пару заглушек
# Создаем заглушку для удаленной функции
if not hasattr(torchaudio, 'set_audio_backend'):
    torchaudio.set_audio_backend = lambda *args, **kwargs: None # type: ignore

# 2. Создаем виртуальный пустой модуль sox_effects в памяти
if "torchaudio.sox_effects" not in sys.modules:
    mock_sox = types.ModuleType("torchaudio.sox_effects")
    
    # Используем setattr, чтобы анализатор IDE не ругался на неизвестные атрибуты
    setattr(mock_sox, "apply_effects_tensor", lambda *args, **kwargs: (None, None))
    setattr(mock_sox, "apply_effects_file", lambda *args, **kwargs: (None, None))

    # Регистрируем модуль в системе
    sys.modules["torchaudio.sox_effects"] = mock_sox
    setattr(torchaudio, "sox_effects", mock_sox)

import wespeaker
# pip install git+https://github.com/wenet-e2e/wespeaker.git
from wespeaker.frontend import TFMelFrontend

# Настройка логирования для продакшена
logger = logging.getLogger(__name__)

class AudioEmbeddingExtractor:
    """Класс для пакетного извлечения эмбеддингов из аудио буферов.
    
    Использует внутреннюю модель wespeaker-voxceleb-resnet34-LM на GPU.
    """

    def __init__(self, model_path: str, device: str, ) -> None:
        """Инициализирует модель WeSpeaker и извлекает торч-модуль для GPU-батчинга.

        Args:
            model_path: Путь к локальной предобученной модели (.pt или .zip)
            batch_size: Размер пакета для инференса.
        """
        self.model_path = model_path
        self.device = device

        try:
            # Загружаем официальную обертку
            speaker_wrapper = wespeaker.load_model(self.model_path)
            
            self.torch_model: torch.nn.Module = speaker_wrapper.model
            self.torch_model.to(self.device)
            self.torch_model.eval()

            self.frontend = TFMelFrontend(
                n_mels=72,
                n_fft=512,
                win_length=400,
                hop_length=160,
                f_min=20,
                f_max=7600,
                do_preemph=True,
                norm_signal=True,
                do_spec_aug=False                
            )
            self.frontend = self.frontend.to(self.device)
            self.frontend.eval()

            self.frontend.register_buffer(
                "flipped_filter",
                torch.FloatTensor([[-1.0, 1.0]]).unsqueeze(1)                
            )

            logger.info(f"Модель WeSpeaker успешно перенесена на GPU ({self.device}) для пакетного инференса.")
        except Exception as e:
            logger.error(f"Не удалось инициализировать PyTorch модель WeSpeaker: {e}")
            raise

    def get_embeddings(
        self, 
        audio_buffers: List[np.ndarray]
    ) -> List[np.ndarray]:
        """Извлекает эмбеддинги для списка аудиомассивов с пакетной обработкой на GPU.

        Args:
            audio_buffers: Список numpy массивов (моно, 16кГц, float32).

        Returns:
            Список соответствующих эмбеддингов np.ndarray в исходном порядке.
        """
        if not audio_buffers:
            return []

        batch = torch.from_numpy(np.stack(audio_buffers))
        batch = batch.to(self.device, non_blocking=True)

        with torch.inference_mode():
            features, _ = self.frontend(batch)
            embeddings = self.torch_model(features) 

            #embeddings_list = [e for e in embeddings.cpu().numpy()]       
            # Нормализация по L2-норме вдоль размерности признаков (обычно dim=1 или dim=-1)
            normalized_embeddings = F.normalize(embeddings, p=2, dim=-1)

            # Быстрый перевод в список NumPy массивов
            embeddings_list = list(normalized_embeddings.cpu().numpy())              

        return embeddings_list

    def get_embedding(
        self, 
        audio_buffer: np.ndarray
    ) -> np.ndarray:
        return self.get_embeddings([audio_buffer])[0]