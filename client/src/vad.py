from typing import Sequence, cast
import numpy as np
import onnxruntime as ort # версия обязательно 1.20.0 - более свежие приводят к ошибкам на относительно старых компах/системах


class VADProcessor:
    """Класс для потокового обнаружения голоса (VAD) с использованием Silero-VAD V6.2+ через ONNX."""

    def __init__(self, model_path: str, threshold: float = 0.5, sample_rate: int = 16000) -> None:
        """
        Инициализация сессии ONNX и внутренних состояний модели.

        Args:
            model_path: Путь к файлу модели ONNX.
            threshold: Порог вероятности, выше которого сегмент считается голосом.
        """
        self.threshold: float = threshold
        self.sample_rate: int = sample_rate
        
        # Оптимальные настройки сессии для продуктивного использования на CPU
        opts: ort.SessionOptions = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        # Инициализация ONNX Runtime сессии
        self._session: ort.InferenceSession = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )

        # Константы геометрии тензоров Silero V6.2
        self._context_size: int = 64
        self.frame_size: int = 512  # 32 мс при 16kHz
        self._state_shape: tuple[int, int, int] = (2, 1, 128)

        # Хранилища для состояний между вызовами чанков
        self._state: np.ndarray = np.zeros(self._state_shape, dtype=np.float32)
        self._context: np.ndarray = np.zeros(self._context_size, dtype=np.float32)
        self._sr_tensor: np.ndarray = np.array([self.sample_rate], dtype=np.int64)

    def voice_detected(self, chunk: np.ndarray) -> bool:
        """
        Определяет наличие голоса в переданном чанке аудио.

        Args:
            chunk: На вход ожидается одномерный массив (512 сэмплов, 16кГц, моно, float32).

        Returns:
            True, если вероятность голоса >= threshold, иначе False.
        """

        # Автоусиление 
        max_vol = np.max(np.abs(chunk))
        if 0.001 < max_vol < 0.1:
            chunk = chunk * (0.2 / max_vol)        

        # Формирование входного вектора с учетом контекста (64 сэмпла истории + 512 текущих = 576)
        # Архитектура V6 требует форму [1, 576]
        input_data: np.ndarray = np.concatenate([self._context, chunk], axis=0)
        input_tensor: np.ndarray = np.expand_dims(input_data, axis=0)

        # Подготовка входных параметров для ONNX сессии
        inputs: dict[str, np.ndarray] = {
            "input": input_tensor,
            "state": self._state,
            "sr": self._sr_tensor
        }

        # Выполнение инференса
        raw_outputs = self._session.run(None, inputs)  
        outputs: Sequence[np.ndarray] = cast(Sequence[np.ndarray], raw_outputs)
        
        out_probability: np.ndarray = outputs[0]  # Форма: [1, 1]
        new_state: np.ndarray = outputs[1]        # Форма: [2, 1, 128]

        # Обновление внутренних состояний для следующей итерации
        self._state = new_state
        self._context = chunk[-self._context_size :].copy()

        # Извлечение скалярного значения вероятности
        speech_prob: float = float(out_probability[0, 0])

        return speech_prob >= self.threshold

    def reset_states(self) -> None:
        """Сброс контекста и скрытых состояний LSTM между независимыми аудио-потоками."""
        self._state = np.zeros(self._state_shape, dtype=np.float32)
        self._context = np.zeros(self._context_size, dtype=np.float32)
