import os
from huggingface_hub import snapshot_download, hf_hub_download

def parse_and_download(config_path="./models.txt"):
    if not os.path.exists(config_path):
        print(f"Configuration file {config_path} not found.")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line_num, line in enumerate(lines, 1):
        line = line.strip()
        
        # Пропускаем пустые строки и комментарии
        if not line or line.startswith("#"):
            continue

        try:
            # Разделяем строку на путь/репозиторий и файл (если есть знак '|')
            if "|" in line:
                repo_part, filename = line.split("|", 1)
                repo_part = repo_part.strip()
                filename = filename.strip()
            else:
                repo_part = line
                filename = None

            # Определяем формат: явный с '<' или неявный по путям
            if "<" in repo_part:
                local_dir, repo_id = repo_part.split("<", 1)
                local_dir = local_dir.strip()
                repo_id = repo_id.strip()
            else:
                # Формат без '<': путь совпадает с ID репозитория
                # Ищем последние два компонента пути для ID репозитория (автор/модель)
                parts = repo_part.replace("\\", "/").split("/")
                if len(parts) < 2:
                    print(f"Error on line {line_num}: Invalid path format for repository ID '{line}'")
                    continue
                repo_id = f"{parts[-2]}/{parts[-1]}"
                local_dir = repo_part

            # Приводим путь к текущей директории
            local_dir = os.path.abspath(local_dir)

            # Логика скачивания
            if filename:
                # Вариант с конкретным файлом
                target_file_path = os.path.join(local_dir, filename)
                if os.path.exists(target_file_path):
                    print(f"[-] File already exists, skipping: {target_file_path}")
                else:
                    print(f"[+] Downloading file '{filename}' from the repository '{repo_id}' в '{local_dir}'...")
                    hf_hub_download(
                        repo_id=repo_id,
                        filename=filename,
                        local_dir=local_dir
                    )
            else:
                # Вариант со всем репозиторием
                # Проверяем, существует ли папка и не пуста ли она
                if os.path.exists(local_dir) and os.listdir(local_dir):
                    print(f"[-] The directory already exists and is not empty; skipping: {local_dir}")
                else:
                    print(f"[+] Downloading the entire repository '{repo_id}' в '{local_dir}'...")
                    snapshot_download(
                        repo_id=repo_id,
                        local_dir=local_dir
                    )

        except Exception as e:
            print(f"[!] Error processing string {line_num} '{line}': {e}")

if __name__ == "__main__":
    parse_and_download()
