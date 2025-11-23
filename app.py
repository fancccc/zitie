import io
import math
import base64
import os
from pathlib import Path
from typing import List, Tuple, Dict

import streamlit as st
from PIL import Image, ImageDraw, ImageFont

import json
import subprocess
from dataloader import PlainDataLoader
DATAS_CONFIG = "./chinese-poetry/loader/datas.json"

# ============== 一些常量 ==============
# A4 纸像素尺寸（竖版），这里用 2480x3508 对应 300dpi
PAGE_WIDTH = 2480
PAGE_HEIGHT = 3508


# ============== Streamlit 基本设置 ==============
st.set_page_config(page_title="田字格字帖生成器", layout="wide")

# 收紧页面上下空白
st.markdown(
    """
    <style>
        .block-container {
            padding-top: 0.5rem;
            padding-bottom: 0.5rem;
        }
        h1, h2, h3 {
            margin-bottom: 0.5rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ============== session_state 初始化 ==============
if "zitie_images" not in st.session_state:
    st.session_state.zitie_images: List[Image.Image] = []
if "zitie_total_pages" not in st.session_state:
    st.session_state.zitie_total_pages: int = 0
if "zitie_current_page" not in st.session_state:
    st.session_state.zitie_current_page: int = 1
if "zitie_pdf_bytes" not in st.session_state:
    st.session_state.zitie_pdf_bytes: bytes = b""

if "poem_choices" not in st.session_state:
    st.session_state.poem_choices: List[Dict[str, str]] = []
if "selected_poem_index" not in st.session_state:
    st.session_state.selected_poem_index: int | None = None
if "zitie_input_text" not in st.session_state:
    st.session_state["zitie_input_text"] = ""

POETRY_REPO_URL = "https://github.com/chinese-poetry/chinese-poetry.git"


def ensure_poetry_repo(repo_dir: str) -> tuple[bool, str]:
    """
    确保本地有 chinese-poetry 仓库：
    - 如果目录不存在：执行 git clone
    - 如果已存在：执行 git pull 更新
    返回 (成功与否, 信息字符串)
    """
    repo_path = Path(repo_dir).expanduser()
    try:
        if not repo_path.exists():
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            # clone
            result = subprocess.run(
                ["git", "clone", POETRY_REPO_URL, str(repo_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return False, f"git clone 失败：{result.stderr}"
            return True, f"已克隆 chinese-poetry 仓库到：{repo_path}"
        else:
            # pull
            result = subprocess.run(
                ["git", "-C", str(repo_path), "pull"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return False, f"git pull 失败：{result.stderr}"
            return True, f"已更新 chinese-poetry 仓库：{repo_path}"
    except FileNotFoundError:
        return False, "找不到 git 命令，请确认已在系统中安装 git。"
    except Exception as e:
        return False, f"更新仓库时出错：{e}"


def load_poems_from_json(json_path: Path) -> List[Dict[str, str]]:
    """
    从 chinese-poetry 的某个 JSON 文件中加载诗词。
    尽量兼容几种常见结构：
    - [ {title, author, paragraphs: []}, ... ]
    - [ {rhythmic, author, paragraphs}, ... ] （宋词）
    - { poems: [...] }
    返回：[{ 'label': '作者《标题》', 'content': '整首诗\n按行拼接' }, ...]
    """
    if not json_path.exists():
        raise FileNotFoundError(f"JSON 文件不存在：{json_path}")

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "poems" in data:
        items = data["poems"]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("无法识别的 JSON 结构：既不是列表，也没有 'poems' 字段。")

    poems: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        # 标题：有 title 用 title，有 rhythmic（词牌）用 rhythmic，再不行用 chapter
        title = item.get("title") or item.get("rhythmic") or item.get("chapter") or "无题"
        author = item.get("author") or item.get("writer") or "佚名"

        paragraphs = (
            item.get("paragraphs")
            or item.get("content")
            or item.get("paragraph")
            or []
        )

        if isinstance(paragraphs, str):
            content = paragraphs
        elif isinstance(paragraphs, list):
            # 去掉空行
            content = "\n".join([p for p in paragraphs if isinstance(p, str) and p.strip()])
        else:
            content = str(paragraphs)

        label = f"{author}《{title}》"
        poems.append({"label": label, "content": content})

    return poems

# ============== 字体相关函数 ==============

def _list_fonts_in_directory(dir_path: str, limit: int = 50) -> List[Dict[str, str]]:
    """
    在指定目录递归查找字体文件，返回列表：
    [{"label": "SimHei (simhei.ttf)", "path": "C:/Windows/Fonts/simhei.ttf"}, ...]
    """
    fonts: List[Dict[str, str]] = []
    p = Path(dir_path).expanduser()

    if not p.is_dir():
        return fonts

    # 允许的字体后缀
    exts = {".ttf", ".ttc", ".otf"}

    for font_file in p.rglob("*"):
        if font_file.suffix.lower() in exts:
            try:
                f = ImageFont.truetype(str(font_file), 20)
                family, style = f.getname()
                label = f"{family} ({font_file.name})"
            except Exception:
                # 即使读取失败，也可以仅用文件名展示
                label = font_file.name
            fonts.append({"label": label, "path": str(font_file)})
            if len(fonts) >= limit:
                break

    return fonts


def discover_fonts(font_dir: str = "./fonts", limit: int = 50) -> Tuple[List[Dict[str, str]], str]:
    """
    优先从 font_dir 查找字体，如果没有就从系统字体目录中查找部分字体。
    返回 (字体列表, 来源说明)
    """
    # 1. 优先使用用户自定义目录
    fonts = _list_fonts_in_directory(font_dir, limit=limit)
    if fonts:
        desc = f"使用自定义字体目录：{Path(font_dir).resolve()}"
        return fonts, desc

    # 2. 尝试从系统字体目录里找一部分字体
    system_dirs = []

    if os.name == "nt":  # Windows
        system_dirs.append(r"C:\Windows\Fonts")
    else:
        # macOS & Linux 常见字体目录
        system_dirs.extend([
            "/System/Library/Fonts",
            "/Library/Fonts",
            str(Path.home() / "Library/Fonts"),
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            str(Path.home() / ".fonts"),
        ])

    fonts_collected: List[Dict[str, str]] = []
    for d in system_dirs:
        if len(fonts_collected) >= limit:
            break
        fonts_in_d = _list_fonts_in_directory(d, limit=limit - len(fonts_collected))
        fonts_collected.extend(fonts_in_d)

    if fonts_collected:
        return fonts_collected, "未在自定义目录找到字体，已从系统字体目录中选择部分字体。"

    # 3. 如果系统字体也没找到（极少见），返回空列表
    return [], "未在指定目录或系统目录中找到可用字体，将使用 Pillow 默认字体。"

@st.cache_resource
def get_plain_loader(config_path: str = DATAS_CONFIG) -> PlainDataLoader:
    return PlainDataLoader(config_path=config_path)
@st.cache_resource
def load_font(font_path: str, size: int):
    """
    加载字体，同时返回字体信息（实际路径、字体族名、样式、来源说明）
    """
    tried_paths = []

    def try_path(path, source_label):
        try:
            font = ImageFont.truetype(path, size)
            family, style = font.getname()
            info = {
                "path": str(Path(path)),
                "family": family,
                "style": style,
                "source": source_label,
            }
            return font, info
        except Exception:
            tried_paths.append(path)
            return None, None

    # 1. 优先尝试用户选中的路径
    if font_path:
        font, info = try_path(font_path, "用户选择字体")
        if font:
            return font, info

    # 2. 常见中文字体作为回退
    fallbacks = [
        "simkai.ttf",    # 楷体
        "simhei.ttf",    # 黑体
        "msyh.ttc",      # 微软雅黑
    ]
    for fb in fallbacks:
        font, info = try_path(fb, f"回退字体（{fb}）")
        if font:
            return font, info

    # 3. 最终回退到 Pillow 默认字体（可能不能正常显示中文）
    font = ImageFont.load_default()
    family, style = (font.getname() if hasattr(font, "getname") else ("Pillow 默认字体", "Regular"))
    info = {
        "path": "Pillow 内置默认字体（可能不支持中文）",
        "family": family,
        "style": style,
        "source": "Pillow 默认字体",
    }
    return font, info


# ============== 画格子相关函数 ==============

def get_grid_color(name: str) -> Tuple[int, int, int]:
    if name == "绿色":
        return (0, 160, 0)
    if name == "红色":
        return (200, 0, 0)
    # 默认黑色
    return (0, 0, 0)


def get_text_color(name: str) -> Tuple[int, int, int]:
    if name == "绿色":
        return (0, 160, 0)
    if name == "红色":
        return (200, 0, 0)
    return (0, 0, 0)


def get_demo_alpha(level: str) -> int:
    """
    描红深浅 -> alpha 值（0~255）
    """
    mapping = {
        "非常深": 220,
        "深": 190,
        "较深": 160,
        "略浅": 130,
        "适中": 110,
        "非常浅": 80,
        "白色（不可见）": 0,
        "空芯": 200,  # 空芯其实应该只画轮廓，这里先用较深颜色
    }
    return mapping.get(level, 110)


def draw_tianzige(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: Tuple[int, int, int]):
    """
    田字格：外框 + 中间十字
    """
    x2, y2 = x + size, y + size
    draw.rectangle([x, y, x2, y2], outline=color, width=2)
    draw.line([x + size // 2, y, x + size // 2, y2], fill=color, width=1)
    draw.line([x, y + size // 2, x2, y + size // 2], fill=color, width=1)


def draw_mizige(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: Tuple[int, int, int]):
    """
    米字格：外框 + 米字对角线 + 中心十字
    """
    x2, y2 = x + size, y + size
    draw.rectangle([x, y, x2, y2], outline=color, width=2)
    draw.line([x, y, x2, y2], fill=color, width=1)
    draw.line([x2, y, x, y2], fill=color, width=1)
    draw.line([x + size // 2, y, x + size // 2, y2], fill=color, width=1)
    draw.line([x, y + size // 2, x2, y + size // 2], fill=color, width=1)


def draw_huigongge(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: Tuple[int, int, int]):
    """
    回宫格：类似回字结构
    """
    x2, y2 = x + size, y + size
    draw.rectangle([x, y, x2, y2], outline=color, width=2)
    margin = size // 6
    draw.rectangle([x + margin, y + margin, x2 - margin, y2 - margin], outline=color, width=1)
    margin2 = margin * 2
    draw.rectangle([x + margin2, y + margin2, x2 - margin2, y2 - margin2], outline=color, width=1)


def draw_square(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: Tuple[int, int, int]):
    x2, y2 = x + size, y + size
    draw.rectangle([x, y, x2, y2], outline=color, width=2)


def draw_jiugongge(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: Tuple[int, int, int]):
    """
    九宫格：3x3 内部分格 + 外框
    """
    x2, y2 = x + size, y + size
    draw.rectangle([x, y, x2, y2], outline=color, width=2)

    step = size // 3
    draw.line([x + step, y, x + step, y2], fill=color, width=1)
    draw.line([x + 2 * step, y, x + 2 * step, y2], fill=color, width=1)
    draw.line([x, y + step, x2, y + step], fill=color, width=1)
    draw.line([x, y + 2 * step, x2, y + 2 * step], fill=color, width=1)


def draw_grid(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    size: int,
    grid_type: str,
    color: Tuple[int, int, int],
):
    if grid_type == "田字格":
        draw_tianzige(draw, x, y, size, color)
    elif grid_type == "米字格":
        draw_mizige(draw, x, y, size, color)
    elif grid_type == "回宫格":
        draw_huigongge(draw, x, y, size, color)
    elif grid_type == "方格":
        draw_square(draw, x, y, size, color)
    elif grid_type == "九宫格":
        draw_jiugongge(draw, x, y, size, color)
    else:
        draw_square(draw, x, y, size, color)


def layout_chars(chars: List[str], repeat: int) -> List[str]:
    result = []
    for ch in chars:
        result.extend([ch] * repeat)
    return result


def generate_single_page_image(
    page_chars: List[str],
    grid_type: str,
    grid_color_name: str,
    text_color_name: str,
    demo_level: str,
    cols: int,
    rows: int,
    font_path: str,
    show_demo: bool = True,
    blank_row_after_each: bool = False,
) -> Image.Image:

    img = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), "white")
    draw = ImageDraw.Draw(img)

    grid_color = get_grid_color(grid_color_name)
    text_color = get_text_color(text_color_name)
    alpha = get_demo_alpha(demo_level)

    margin_x = 150
    margin_y = 250
    usable_width = PAGE_WIDTH - margin_x * 2
    usable_height = PAGE_HEIGHT - margin_y * 2

    cell_size = min(usable_width // cols, usable_height // rows)

    offset_x = (PAGE_WIDTH - cell_size * cols) // 2
    offset_y = (PAGE_HEIGHT - cell_size * rows) // 2

    font_size = int(cell_size * 0.7)
    font, _ = load_font(font_path, font_size)

    text_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)

    index = 0
    # max_demo_cells 只计算“有字”的格子数量（用于安全判断）
    if blank_row_after_each:
        demo_rows_per_page = math.ceil(rows / 2)
    else:
        demo_rows_per_page = rows
    max_demo_cells = cols * demo_rows_per_page

    for r in range(rows):
        for c in range(cols):
            x = offset_x + c * cell_size
            y = offset_y + r * cell_size

            # 画格子
            draw_grid(draw, x, y, cell_size, grid_type, grid_color)

            # 这一行是不是“示范行”
            if blank_row_after_each:
                is_demo_row = (r % 2 == 0)   # 0,2,4,... 行有字；1,3,5,... 行空行
            else:
                is_demo_row = True

            if (
                show_demo
                and is_demo_row
                and index < len(page_chars)
                and index < max_demo_cells
            ):
                ch = page_chars[index]
                index += 1

                # 判断是否标点，使用不同字号 & 轻微位置微调
                # if is_punctuation(ch):
                #     font = punct_font
                #     offset_y_char = int(cell_size * 0.05)
                # else:
                #     font = main_font
                offset_y_char = 0

                # 以格子中心为基准，anchor="mm" 居中
                cx = x + cell_size // 2
                cy = y + cell_size // 2 + offset_y_char

                text_draw.text(
                    (cx, cy),
                    ch,
                    font=font,
                    fill=(text_color[0], text_color[1], text_color[2], alpha),
                    anchor="mm",
                )


    img = Image.alpha_composite(img.convert("RGBA"), text_layer)
    return img.convert("RGB")


def generate_multi_page_images(
    text: str,
    grid_type: str,
    grid_color_name: str,
    text_color_name: str,
    demo_level: str,
    cols: int,
    rows: int,
    repeat_each: int,
    font_path: str,
    show_demo: bool = True,
    fill_last_page: bool = True,
    blank_row_after_each: bool = False,
) -> List[Image.Image]:
    """
    根据总字数和每页容量，生成多页图片
    blank_row_after_each=True 时：每一行示范字后面跟一行空行，只绘制偶数行（0,2,4,...) 有字。
    """
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return []

    # 每个字重复 N 次
    chars = layout_chars(chars, repeat_each)

    # 计算每页“有字”的行数
    if blank_row_after_each:
        # rows 行中有 ceil(rows / 2) 行是示范字
        demo_rows_per_page = math.ceil(rows / 2)
    else:
        demo_rows_per_page = rows

    # 每页最多能放多少个“示范字”
    page_capacity = cols * demo_rows_per_page
    total_pages = max(1, math.ceil(len(chars) / page_capacity))

    images: List[Image.Image] = []

    for page_idx in range(total_pages):
        start = page_idx * page_capacity
        end = start + page_capacity
        page_chars = chars[start:end]

        # 填充尾页：用最后一个字把剩余“示范格子”填满
        if fill_last_page and len(page_chars) < page_capacity and page_chars:
            last_ch = page_chars[-1]
            page_chars = page_chars + [last_ch] * (page_capacity - len(page_chars))

        img = generate_single_page_image(
            page_chars=page_chars,
            grid_type=grid_type,
            grid_color_name=grid_color_name,
            text_color_name=text_color_name,
            demo_level=demo_level,
            cols=cols,
            rows=rows,
            font_path=font_path,
            show_demo=show_demo,
            blank_row_after_each=blank_row_after_each,  # 传下去
        )
        images.append(img)

    return images


def images_to_pdf(images: List[Image.Image]) -> bytes:
    if not images:
        return b""
    buf = io.BytesIO()
    rgb_imgs = [im.convert("RGB") for im in images]
    first, rest = rgb_imgs[0], rgb_imgs[1:]
    first.save(buf, format="PDF", save_all=True, append_images=rest)
    buf.seek(0)
    return buf.getvalue()


def render_image_scrollable(img: Image.Image, scale_percent: int = 70, height: int = 600):
    """
    使用 CSS 控制缩放比例（width: {scale_percent}%），
    外层容器固定高度、内部滚动。
    """
    scale_percent = max(10, scale_percent)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")

    st.markdown(
        f"""
        <div style="
            height: {height}px;
            overflow: auto;
            border: 1px solid #ccc;
            padding: 4px;
            background-color: #f8f8f8;
        ">
            <img src="data:image/png;base64,{encoded}"
                 style="width: {scale_percent}%; height: auto; display: block; margin: 0 auto;"/>
        </div>
        """,
        unsafe_allow_html=True,
    )

def use_poem_as_input(poem: str):
    """按钮回调：把选中的诗词写入主输入框的 session_state"""
    st.session_state["zitie_input_text"] = poem

# ============== Streamlit UI ==============

st.title("📚 田字格字帖生成器")

st.markdown(
    """
- 支持田字格、米字格、回宫格、方格、九宫格  
- 自动按 A4 分页生成多页字帖  
- 字体目录：默认使用 `./fonts`，如果没有字体则自动从系统字体中挑选  
- 可以下拉选择已发现的字体  
"""
)

# text = st.text_area("请输入想要生成的汉字 / 词语 / 句子：", height=100)
text = st.text_area(
    "请输入想要生成的汉字 / 词语 / 句子：",
    height=100,
    key="zitie_input_text",   # 以后我们通过 session_state 来写入
)
# 三列宽度稍微调整一下：右侧列更宽一点，放模式 + 字体
col1, col2, col3 = st.columns([1.1, 1.1, 1.5])

with col1:
    st.markdown("#### 格子与颜色")
    grid_type = st.selectbox(
        "田格类型：",
        ["田字格", "米字格", "回宫格", "方格", "九宫格"],
        index=1,
    )
    # 横向排布，减少纵向空间占用
    grid_color_name = st.radio(
        "田格颜色：",
        ["黑色", "绿色", "红色"],
        index=2,
        horizontal=True,
    )
    text_color_name = st.radio(
        "文字颜色：",
        ["黑色", "绿色", "红色"],
        index=0,
        horizontal=True,
    )

with col2:
    st.markdown("#### 行列与描红")
    demo_level = st.selectbox(
        "描红深浅：",
        ["非常深", "深", "较深", "略浅", "适中", "非常浅", "白色（不可见）", "空芯"],
        index=4,
    )
    cols_num = st.slider("每行格子数", min_value=5, max_value=20, value=10, step=1)
    rows_num = st.slider("每页行数", min_value=5, max_value=20, value=14, step=1)

with col3:
    st.markdown("#### 字帖模式与字体")

    # 上半部分：与内容相关的选项
    repeat_each = st.slider("每个字重复次数", min_value=1, max_value=10, value=1, step=1)
    show_demo = st.checkbox("显示示范字（描红）", value=True)
    fill_last_page = st.checkbox("填充尾页（用最后一个字补满空格）", value=False)

    mode = st.selectbox(
        "字帖模式",
        ["普通模式（每行都有字）", "临摹模式（每行后留一空行）"],
        index=0,
    )
    blank_row_after_each = (mode == "临摹模式（每行后留一空行）")

    st.caption("只练字格：取消勾选“显示示范字”。")

    # 字体相关收进折叠面板，减少视觉压力
    with st.expander("字体设置（目录与字体选择）", expanded=False):
        font_dir = st.text_input("字体目录路径", "./fonts")
        font_options, font_source_desc = discover_fonts(font_dir)

        if not font_options:
            st.warning(font_source_desc)
            selected_font_path = ""
            font_info = {
                "family": "Pillow 默认字体",
                "style": "",
                "path": "内置默认字体",
                "source": "Pillow 默认字体",
            }
        else:
            labels = [opt["label"] for opt in font_options]
            selected_label = st.selectbox("选择字体", labels)
            selected_font_path = next(
                opt["path"] for opt in font_options if opt["label"] == selected_label
            )

            # 获取字体信息（包括回退情况）
            _, font_info = load_font(selected_font_path, 40)

        st.caption(
            f"字体来源：{font_source_desc}  \n"
            f"实际使用字体：**{font_info.get('family', '')} {font_info.get('style', '')}** ｜ "
            f"路径：`{font_info.get('path', '')}`"
        )

st.markdown("---")
st.subheader("📚 诗词素材（来自 chinese-poetry 仓库，可选）")

with st.expander("从 chinese-poetry 仓库选择一首诗/词填入字帖", expanded=False):
    # 1. 仓库路径设置
    default_repo_dir = "./chinese-poetry"
    repo_dir = st.text_input("仓库本地路径（git clone 目标路径）", default_repo_dir)

    col_repo_btn1, col_repo_btn2 = st.columns(2)
    with col_repo_btn1:
        if st.button("📥 克隆 / 更新 chinese-poetry 仓库", use_container_width=True):
            ok, msg = ensure_poetry_repo(repo_dir)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    with col_repo_btn2:
        st.caption("确保本机已安装 git 命令。")

    st.markdown("----")

    # 2. 选择要使用的 JSON 文件路径
    st.markdown("选择一个 JSON 诗词文件（相对于仓库根目录）：")
    try:
        loader = get_plain_loader("./chinese-poetry/loader/datas.json")
        dataset_keys = list(loader.datasets.keys())
    except Exception as e:
        loader = None
        dataset_keys = []
        st.error(f"加载 PlainDataLoader 失败：{e}")

    if not loader or not dataset_keys:
        st.info("尚未配置或加载数据集，请检查 ./chinese-poetry/loader/datas.json 和 chinese-poetry 仓库位置。")
    else:
        # 2. 选择数据集，比如：tang-poetry / song-poetry / wudai-huajianji 等
        ds_name = st.selectbox("选择数据集：", dataset_keys)

        # 可选：限制一次读取的数量，避免太大
        max_count = st.number_input("最多读取前 N 首（避免一次性超大）", min_value=50, max_value=5000, value=1000, step=50)

        # 3. 加载诗词（按“首”返回）
        poems_cache_key = f"poems_{ds_name}"
        if st.button("🔍 从该数据集加载诗词列表", use_container_width=True):
            try:
                # print(ds_name)
                poems_texts = loader.poems_as_text(ds_name)
                # 简单截断到 max_count
                poems_texts = poems_texts[: int(max_count)]
                st.session_state[poems_cache_key] = poems_texts
                st.success(f"已从 {ds_name} 加载 {len(poems_texts)} 首诗词。")
            except Exception as e:
                st.error(f"加载诗词失败：{e}")
                print(e)

        poems_texts = st.session_state.get(poems_cache_key, [])

        if poems_texts:
            st.write(f"当前数据集中缓存了 {len(poems_texts)} 首。")

            # 简单的索引选择，后续你可以改成搜索或随机
            idx = st.number_input(
                "选择第几首（从 0 开始）",
                min_value=0,
                max_value=len(poems_texts) - 1,
                value=0,
                step=1,
            )
            current_poem = poems_texts[int(idx)]

            st.text_area(
                "诗词预览：",
                value=current_poem,
                height=160,
                key="poem_preview",
            )

            st.button(
                "✅ 使用这首诗词作为字帖内容",
                use_container_width=True,
                on_click=use_poem_as_input,
                args=(current_poem,),
            )

st.markdown("---")
# raw_text = st.session_state.get("zitie_input_text", "")
# text = raw_text
text = st.session_state.zitie_input_text
# 点击生成按钮时：生成字帖，并存入 session_state
if st.button("✨ 生成字帖", type="primary", use_container_width=True):
    if not text.strip():
        st.warning("请先输入要生成的内容。")
    else:
        images = generate_multi_page_images(
            text=text,
            grid_type=grid_type,
            grid_color_name=grid_color_name,
            text_color_name=text_color_name,
            demo_level=demo_level,
            cols=cols_num,
            rows=rows_num,
            repeat_each=repeat_each,
            font_path=selected_font_path,
            show_demo=show_demo,
            fill_last_page=fill_last_page,
            blank_row_after_each=blank_row_after_each,
        )

        if not images:
            st.error("生成失败，请检查输入内容和字体设置。")
        else:
            st.session_state.zitie_images = images
            st.session_state.zitie_total_pages = len(images)
            st.session_state.zitie_current_page = 1
            st.session_state.zitie_pdf_bytes = images_to_pdf(images)
            st.success(f"生成完成，共 {len(images)} 页字帖。")

# 预览区域
if st.session_state.zitie_images:
    total_pages = st.session_state.zitie_total_pages
    current_page = st.session_state.zitie_current_page

    st.markdown("---")
    st.subheader("🖼 字帖预览")

    nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])

    with nav_col1:
        if st.button("⬅ 上一页", key="prev_page", use_container_width=True) and current_page > 1:
            st.session_state.zitie_current_page -= 1

    with nav_col2:
        st.markdown(
            f"<div style='text-align:center; font-weight:bold;'>第 {st.session_state.zitie_current_page} 页 / 共 {total_pages} 页</div>",
            unsafe_allow_html=True,
        )

    with nav_col3:
        if st.button("下一页 ➡", key="next_page", use_container_width=True) and current_page < total_pages:
            st.session_state.zitie_current_page += 1

    current_page = st.session_state.zitie_current_page

    scale_percent = st.slider(
        "预览缩放比例（仅影响屏幕显示）",
        min_value=30,
        max_value=150,
        value=70,
        step=5,
    )

    current_img = st.session_state.zitie_images[current_page - 1]
    render_image_scrollable(current_img, scale_percent=scale_percent, height=600)

    png_buf = io.BytesIO()
    current_img.save(png_buf, format="PNG")
    png_buf.seek(0)
    st.download_button(
        label=f"📥 下载当前页 PNG（第 {current_page} 页）",
        data=png_buf,
        file_name=f"tianzige_page_{current_page}.png",
        mime="image/png",
        use_container_width=True,
    )

    if st.session_state.zitie_pdf_bytes:
        st.download_button(
            label="📄 下载全部页面 PDF 字帖",
            data=st.session_state.zitie_pdf_bytes,
            file_name="tianzige_all_pages.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
