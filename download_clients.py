import urllib.request

url = "https://githubusercontent.com"
output_filename = "README.md"

def url_file_download(title:str, url: str, output_filename: str):
    try:
        print(title)
        urllib.request.urlretrieve(url, output_filename)
        print("Файл успешно скачан!")
    except Exception as e:
        print(f"Ошибка при скачивании: {e}")
        
if __name__ == "__main__":
    url_file_download("Загрузка клиента для windows", "", "./site/assets/ListeLog_win.exe")
    url_file_download("Загрузка клиента для linux", "", "./site/assets/ListeLog_lnx.run")