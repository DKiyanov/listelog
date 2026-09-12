воркер
обрабатывает результаты работы moss
на входе аудио файл (до 20 сек) и файл с сегментацией по спикерам
извлекает эмбендинги спикеров в сегментах
формирует общий список спикеров от всего аудио с присвоением спикеров к сегментам

установка
Клонировать проект

Установить venv
py -3.11 -m venv .venv

Активировать venv
.venv\Scripts\activate

проверка
pip --version
должен вернуть pip (python 3.11)

Установить зависимости   
pip install -r requirements.txt   

pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128   

pip install git+https://github.com/wenet-e2e/wespeaker.git   

pip install git+https://github.com/DKiyanov/listelog.git@main#subdirectory=workers/listelog_pyworker_lib   
или   
pip install -e ../listelog_pyworker_lib   

Запуск    
python worker_spkemb_moss.py   