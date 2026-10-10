#!/bin/bash
# Build script for ListeLog client on Linux using PyInstaller + Makeself

set -e  # Остановка при ошибках

# === Цвета для вывода ===
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# === Определение каталога проекта ===
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
log_info "Каталог проекта: $PROJECT_DIR"

# === Поиск виртуального окружения ===
VENV_DIR=""
if [ -d "$PROJECT_DIR/.linux_venv" ]; then
    VENV_DIR="$PROJECT_DIR/.linux_venv"
elif [ -d "$PROJECT_DIR/.venv" ]; then
    VENV_DIR="$PROJECT_DIR/.venv"
else
    log_error "Виртуальное окружение не найдено (.linux_venv или .venv)"
    exit 1
fi
log_ok "Найдено виртуальное окружение: $VENV_DIR"

# === Активация виртуального окружения ===
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
log_ok "Виртуальное окружение активировано: $(which python)"

# === Переменные сборки ===
APP_NAME="ListeLog"
DIST_NAME="ListeLog_lnx"
INSTALL_DIR="ListeLog"

WORKPATH="xbuild/linux/build"
DISTPATH="xbuild/linux/dist"
PKG_STAGE="$PROJECT_DIR/xbuild/linux/pkg_stage"
OUTPUT_DIR="$PROJECT_DIR/xbuild/linux"
OUTPUT_FILE="$OUTPUT_DIR/${DIST_NAME}.run"
SPEC_FILE="client_linux.spec"


# === Установка переменной окружения ===
export FLET_VIEW_PATH="${PROJECT_DIR}/flet_client/empty"
log_info "FLET_VIEW_PATH=$FLET_VIEW_PATH"

# === Очистка предыдущих сборок ===
log_info "Очистка предыдущих сборок..."
rm -rf "$PROJECT_DIR/$WORKPATH"
rm -rf "$PROJECT_DIR/$DISTPATH"
rm -rf "$PKG_STAGE"
rm -f  "$OUTPUT_FILE"
log_ok "Очистка завершена"

# === Проверка зависимостей ===
if ! command -v pyinstaller &>/dev/null; then
    log_error "PyInstaller не установлен в виртуальном окружении"
    log_info  "Установите: pip install pyinstaller"
    exit 1
fi

if ! command -v makeself &>/dev/null; then
    log_error "makeself не установлен в системе"
    log_info  "Установите: sudo apt install makeself (Debian/Ubuntu)"
    exit 1
fi

# === PyInstaller ===
log_info "Запуск PyInstaller..."
pyinstaller \
    --workpath "$WORKPATH" \
    --distpath "$DISTPATH" \
    --noconfirm \
    "$SPEC_FILE"

if [ ! -d "$PROJECT_DIR/$DISTPATH/$APP_NAME" ]; then
    log_error "PyInstaller не создал каталог $DISTPATH/$APP_NAME"
    exit 1
fi
log_ok "PyInstaller завершён: $DISTPATH/$APP_NAME"

# === Подготовка установочного скрипта для Makeself ===
log_info "Подготовка установочного скрипта..."

# Создаём временный каталог для сборки пакета
mkdir -p "$PKG_STAGE"

# Копируем собранное приложение
cp -a "$PROJECT_DIR/$DISTPATH/$APP_NAME" "$PKG_STAGE/$APP_NAME"

# Формируем install.sh, который выполнится при запуске .run файла
cat > "$PKG_STAGE/install.sh" <<'INSTALL_EOF'
#!/bin/bash
# Installer script (executed by makeself)

INSTALL_DIR="ListeLog"
TARGET_BASE="${HOME}/.local/opt"
TARGET_DIR="${TARGET_BASE}/${INSTALL_DIR}"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"

echo "=== Установка ListeLog ==="

# Копирование файлов
mkdir -p "$TARGET_BASE"
rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
cp -a "./ListeLog/." "$TARGET_DIR/"
chmod +x "$TARGET_DIR/ListeLog" 2>/dev/null || true

# Симлинк в ~/.local/bin
mkdir -p "$BIN_DIR"
ln -sf "$TARGET_DIR/ListeLog" "$BIN_DIR/listelog"

# Desktop-файл
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/listelog.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ListeLog
Exec=${TARGET_DIR}/ListeLog
Terminal=false
Categories=Utility;
EOF
chmod +x "$DESKTOP_DIR/listelog.desktop"

# Иконка (если есть в сборке)
if [ -f "$TARGET_DIR/_internal/assets/icon.png" ]; then
    mkdir -p "$ICON_DIR"
    cp "$TARGET_DIR/_internal/assets/icon.png" "$ICON_DIR/listelog.png"
fi

# Обновление кэша иконок/десктопа (если доступно)
command -v update-desktop-database &>/dev/null && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache -f -t "$ICON_DIR/.." 2>/dev/null || true

# Разрешаем запуск ярлыка в современных DE (например, Ubuntu/GNOME требует доверия к файлу)
if command -v gio &> /dev/null; then
    gio set "$DESKTOP_DIR/listelog.desktop" metadata::trusted true
fi

echo "Установлено в: $TARGET_DIR"
echo "Симлинк: $BIN_DIR/listelog"

# === создание main_config.json ===
# содержащий url к серверу полученный из имени файла дистрибутива
echo
echo "=== создание main_config.json ==="

# $0 содержит полный путь к запущенному .run файлу. Получаем только имя файла:
FILENAME=$(basename "$0")

# Проверяем наличие управляющих символов и формируем протокол
if [[ "$FILENAME" == *%* ]]; then
    # Вырезаем всё от % до .run
    EXTRACTED=$(echo "$FILENAME" | sed -e 's/.*%//' -e 's/\.run.*//')
    PROTOCOL="http://"
elif [[ "$FILENAME" == *\$* ]]; then
    # Вырезаем всё от $ до .run (экранируем $)
    EXTRACTED=$(echo "$FILENAME" | sed -e 's/.*\///' -e 's/.*\$//' -e 's/\.run.*//')
    PROTOCOL="https://"
fi

if [[ -n "$PROTOCOL" ]]; then
    # Заменяем запятые на точки, а точки с запятой на двоеточия
    CLEANED_URL=$(echo "$EXTRACTED" | tr ',' '.' | tr ';' ':')
    BASE_URL="${PROTOCOL}${CLEANED_URL}"

    # Создаем main_config.json в текущем каталоге (куда распаковался архив)
    cat <<EOF > main_config.json
    {
      "base_url": "$BASE_URL"
    }
    EOF

    echo "Конфигурация успешно создана. Сервер: $BASE_URL"
else
    echo "Имя файла не содержит url сервера"
fi

# === Проверка ffmpeg ===
echo
echo "=== Проверка ffmpeg ==="
if command -v ffmpeg &>/dev/null; then
    echo "ffmpeg найден: $(command -v ffmpeg)"
else
    echo "ВНИМАНИЕ: ffmpeg не найден в системе!"
    echo "ListeLog требует ffmpeg для корректной работы."
    echo
    read -r -p "Установить ffmpeg сейчас? [y/N]: " ANSWER
    case "$ANSWER" in
        [yY]|[yY][eE][sS])
            if command -v apt &>/dev/null; then
                sudo apt update && sudo apt install -y ffmpeg
            elif command -v dnf &>/dev/null; then
                sudo dnf install -y ffmpeg
            elif command -v pacman &>/dev/null; then
                sudo pacman -S --noconfirm ffmpeg
            elif command -v zypper &>/dev/null; then
                sudo zypper install -y ffmpeg
            else
                echo "Не удалось определить пакетный менеджер."
                echo "Установите ffmpeg вручную: https://ffmpeg.org/download.html"
            fi
            ;;
        *)
            echo "Пропущено. Установите ffmpeg вручную перед запуском."
            ;;
    esac
fi

echo
echo "=== Установка завершена ==="

echo "=== Запуск приложения ==="
"$INSTALL_DIR/listelog" &
INSTALL_EOF

chmod +x "$PKG_STAGE/install.sh"

# Создаем подкаталог, если он не существует (-p предотвращает ошибку, если папка уже есть)
mkdir -p "$OUTPUT_DIR"

# === Makeself ===
log_info "Создание самораспаковывающегося архива..."
makeself --gzip \
    "$PKG_STAGE" \
    "$OUTPUT_FILE" \
    "ListeLog installer" \
    ./install.sh

if [ ! -f "$OUTPUT_FILE" ]; then
    log_error "Не удалось создать ${DIST_NAME}.run"
    exit 1
fi
chmod +x "$OUTPUT_FILE"
log_ok "Создан дистрибутив: ${DIST_NAME}.run"

# === Копирование в ../site/assets если существует ===
SITE_ASSETS="$PROJECT_DIR/../site/assets"
if [ -d "$SITE_ASSETS" ]; then
    cp -f "$OUTPUT_FILE" "$SITE_ASSETS/"
    log_ok "Скопирован в: $SITE_ASSETS/${DIST_NAME}.run"
else
    log_info "Каталог $SITE_ASSETS не найден — копирование пропущено"
fi

echo
log_ok "Сборка завершена успешно!"
log_info "Дистрибутив: $OUTPUT_FILE"
log_info "Размер: $(du -h "$OUTPUT_FILE" | cut -f1)"