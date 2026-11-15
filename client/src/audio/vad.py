from typing import Sequence, cast
import numpy as np
import onnxruntime as ort # версия обязательно 1.20.0 - более свежие приводят к ошибкам на относительно старых компах/системах



class VADProcessor:
    """Класс для потокового обнаружения голоса (VAD) с использованием Silero-VAD V6.2+ через ONNX."""
    _vad_session: ort.InferenceSession | None = None

    # Константы геометрии тензоров Silero V6.2
    _context_size: int = 64
    _frame_size: int = 512  # 32 мс
    _sample_rate: int = 16000
    _state_shape: tuple[int, int, int] = (2, 1, 128)
    _sr_tensor: np.ndarray = np.array([_sample_rate], dtype=np.int64)

    @staticmethod
    def init(model_path: str) -> None:
        if VADProcessor._vad_session: return

        # Оптимальные настройки сессии для продуктивного использования на CPU
        opts: ort.SessionOptions = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        # Инициализация ONNX Runtime сессии
        VADProcessor._vad_session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )

    def __init__(self) -> None:
        # Хранилища для состояний между вызовами чанков
        self._state: np.ndarray = np.zeros(VADProcessor._state_shape, dtype=np.float32)
        self._context: np.ndarray = np.zeros(VADProcessor._context_size, dtype=np.float32)


    def is_voice(self, chunk: np.ndarray) -> float :
        """
        Определяет наличие голоса в переданном чанке аудио.

        Args:
            chunk: На вход ожидается одномерный массив (512 сэмплов, 16кГц, моно, float32).

        Returns:
            Вероятность наличия голоса
        """       

        assert VADProcessor._vad_session

        # Формирование входного вектора с учетом контекста (64 сэмпла истории + 512 текущих = 576)
        # Архитектура V6 требует форму [1, 576]
        input_data: np.ndarray = np.concatenate([self._context, chunk], axis=0)
        input_tensor: np.ndarray = np.expand_dims(input_data, axis=0)

        # Подготовка входных параметров для ONNX сессии
        inputs: dict[str, np.ndarray] = {
            "input": input_tensor,
            "state": self._state,
            "sr": VADProcessor._sr_tensor
        }

        # Выполнение инференса
        raw_outputs = VADProcessor._vad_session.run(None, inputs)  
        outputs: Sequence[np.ndarray] = cast(Sequence[np.ndarray], raw_outputs)
        
        out_probability: np.ndarray = outputs[0]  # Форма: [1, 1]
        new_state: np.ndarray = outputs[1]        # Форма: [2, 1, 128]

        # Обновление внутренних состояний для следующей итерации
        self._state = new_state
        self._context = chunk[-VADProcessor._context_size :].copy()

        # Извлечение скалярного значения вероятности
        speech_prob: float = float(out_probability[0, 0])

        return speech_prob

    def reset_states(self) -> None:
        """Сброс контекста и скрытых состояний LSTM между независимыми аудио-потоками."""
        self._state = np.zeros(VADProcessor._state_shape, dtype=np.float32)
        self._context = np.zeros(VADProcessor._context_size, dtype=np.float32)
