"""
PWA 아이콘 생성기 (표준 라이브러리만 사용 — PIL 불필요).

docs/ 에 다음 PNG를 생성한다:
  - icon-192.png        (manifest, purpose any)
  - icon-512.png        (manifest, purpose any)
  - icon-512-maskable.png (manifest, purpose maskable — 중앙 60% 안전영역)
  - apple-touch-icon.png  (180x180, iOS 홈화면)

디자인: 다크 배경(#1c1c1a) + 중앙 상승 막대 4개(자산 성장 모티프). 풀블리드 정사각형
(iOS/안드로이드가 자체 마스크를 적용하므로 자체 라운딩/투명 여백 없음).

재실행하면 덮어쓴다. 색/모티프 조정 후 `py scripts/make_icons.py`.
"""
import struct
import zlib
import pathlib

BG = (0x1C, 0x1C, 0x1A)
BARS = [(0x37, 0x8A, 0xDD), (0x7F, 0x77, 0xDD), (0x1D, 0x9E, 0x75), (0xD4, 0x53, 0x7E)]


def _render(size: int) -> bytes:
    w = h = size
    n = len(BARS)
    x0, x1 = int(w * 0.24), int(w * 0.76)
    region = x1 - x0
    gap = max(1, region // (n * 5))
    bw = (region - gap * (n - 1)) // n
    base_y = int(h * 0.74)
    top_min = int(h * 0.30)
    span = base_y - top_min
    heights = [int(span * (0.42 + 0.58 * (i / (n - 1)))) for i in range(n)]
    # 막대 x-구간 → 색 인덱스 (열 단위 lookup)
    col_bar = [-1] * w
    for i in range(n):
        bx0 = x0 + i * (bw + gap)
        for x in range(bx0, min(bx0 + bw, w)):
            col_bar[x] = i

    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type 0 (None)
        for x in range(w):
            i = col_bar[x]
            if i >= 0 and (base_y - heights[i]) <= y < base_y:
                raw += bytes(BARS[i])
            else:
                raw += bytes(BG)
    return bytes(raw)


def _png(size: int, path: pathlib.Path) -> None:
    raw = _render(size)
    comp = zlib.compress(raw, 9)

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", comp)
                     + chunk(b"IEND", b""))
    print(f"  {path.name} ({size}x{size})")


def main() -> None:
    docs = pathlib.Path(__file__).parent.parent / "docs"
    docs.mkdir(exist_ok=True)
    print("PWA 아이콘 생성:")
    _png(192, docs / "icon-192.png")
    _png(512, docs / "icon-512.png")
    _png(512, docs / "icon-512-maskable.png")
    _png(180, docs / "apple-touch-icon.png")
    print("완료.")


if __name__ == "__main__":
    main()
