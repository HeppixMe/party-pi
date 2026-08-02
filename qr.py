#!/usr/bin/env python3
"""
QR-Code-Erzeugung mit reiner Standardbibliothek.

Gerade genug von ISO/IEC 18004, um eine LAN-Adresse wie
"http://192.168.178.42/mitmachen" als scannbaren Code auszugeben:

  - Byte-Modus (ISO-8859-1 bzw. UTF-8, wie es jedes Handy liest)
  - Fehlerkorrektur-Stufe M (~15 % Reserve, der uebliche Kompromiss)
  - Versionen 1 bis 10, automatisch die kleinste passende
  - Reed-Solomon ueber GF(256), Maskenwahl nach den Bewertungsregeln der Norm
  - Ausgabe als SVG: beliebig skalierbar, wenige Kilobyte, auf dem Fernseher
    gestochen scharf

Alleine benutzbar:

    python3 qr.py "http://beispiel/mitmachen" > code.svg
    python3 qr.py --text "http://beispiel/mitmachen"   # als ASCII-Bild
"""

from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# Tabellen aus der Norm
# ---------------------------------------------------------------------------

# Gesamtzahl der Codewoerter je Version (unabhaengig von der Fehlerkorrektur).
TOTAL_CODEWORDS = {
    1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
    6: 172, 7: 196, 8: 242, 9: 292, 10: 346,
}

# Aufbau fuer Stufe M je Version:
#   (Fehlerkorrektur-Codewoerter je Block, [(Blockanzahl, Datencodewoerter je Block), ...])
# Die Datenmenge wird in unterschiedlich grosse Bloecke aufgeteilt, damit ein
# beschaedigter Bereich nie einen ganzen Block mitreisst.
ECC_M = {
    1:  (10, [(1, 16)]),
    2:  (16, [(1, 28)]),
    3:  (26, [(1, 44)]),
    4:  (18, [(2, 32)]),
    5:  (24, [(2, 43)]),
    6:  (16, [(4, 27)]),
    7:  (18, [(4, 31)]),
    8:  (22, [(2, 38), (2, 39)]),
    9:  (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}

# Mittelpunkte der Ausrichtungsmuster je Version.
ALIGNMENT_CENTERS = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
    9: [6, 26, 46],
    10: [6, 28, 50],
}

# Bitmuster der Fehlerkorrektur-Stufen im Formatfeld.
ECC_INDICATOR = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}

MODE_BYTE = 0b0100
PAD_BYTES = (0xEC, 0x11)  # feste Fuellbytes der Norm, abwechselnd


# ---------------------------------------------------------------------------
# Rechnen in GF(256) - die Arithmetik hinter Reed-Solomon
# ---------------------------------------------------------------------------
def _build_tables():
    """Logarithmus- und Exponentialtabelle fuer GF(256), Polynom 0x11D."""
    exp = [0] * 512
    log = [0] * 256
    value = 1
    for i in range(255):
        exp[i] = value
        log[value] = i
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


GF_EXP, GF_LOG = _build_tables()


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def rs_generator(degree: int) -> list[int]:
    """Generatorpolynom (x - a^0)(x - a^1)...(x - a^(degree-1))."""
    poly = [1]
    for i in range(degree):
        poly.append(0)
        factor = GF_EXP[i]
        for index in range(len(poly) - 1, 0, -1):
            poly[index] ^= gf_mul(poly[index - 1], factor)
    return poly


def rs_encode(data: list[int], ec_count: int) -> list[int]:
    """Fehlerkorrektur-Codewoerter zu einem Datenblock berechnen."""
    generator = rs_generator(ec_count)
    remainder = [0] * ec_count
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        if factor:
            for index in range(ec_count):
                remainder[index] ^= gf_mul(generator[index + 1], factor)
    return remainder


# ---------------------------------------------------------------------------
# Daten in Codewoerter verwandeln
# ---------------------------------------------------------------------------
def capacity_bytes(version: int) -> int:
    """Wie viele Nutzbytes passen in diese Version bei Stufe M?"""
    ec_per_block, groups = ECC_M[version]
    data_codewords = sum(count * size for count, size in groups)
    header_bits = 4 + (8 if version < 10 else 16)
    return data_codewords - (header_bits + 7) // 8


def choose_version(payload: bytes) -> int:
    for version in sorted(ECC_M):
        if len(payload) <= capacity_bytes(version):
            return version
    raise ValueError(
        f"{len(payload)} Bytes passen in keine unterstuetzte Version "
        f"(hoechstens {capacity_bytes(max(ECC_M))})"
    )


def build_data_codewords(payload: bytes, version: int) -> list[int]:
    """Kopf, Nutzdaten, Abschluss und Fuellbytes zu Datencodewoertern."""
    ec_per_block, groups = ECC_M[version]
    data_codewords = sum(count * size for count, size in groups)
    capacity_bits = data_codewords * 8

    bits: list[int] = []

    def push(value: int, length: int) -> None:
        for shift in range(length - 1, -1, -1):
            bits.append((value >> shift) & 1)

    push(MODE_BYTE, 4)
    push(len(payload), 8 if version < 10 else 16)
    for byte in payload:
        push(byte, 8)

    # Abschlusszeichen, so weit noch Platz ist.
    push(0, min(4, capacity_bits - len(bits)))
    # Auf volle Bytes auffuellen.
    if len(bits) % 8:
        push(0, 8 - len(bits) % 8)

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    # Rest mit den festen Fuellbytes der Norm belegen, abwechselnd EC/11.
    pad_index = 0
    while len(codewords) < data_codewords:
        codewords.append(PAD_BYTES[pad_index % 2])
        pad_index += 1
    return codewords


def interleave(codewords: list[int], version: int) -> list[int]:
    """
    Datenbloecke und Fehlerkorrekturbloecke verschraenken.

    Die Norm verlangt, dass die Bloecke reihum je ein Codewort abgeben - so
    verteilt sich ein Kratzer auf dem Code auf alle Bloecke statt einen davon
    komplett zu zerstoeren.
    """
    ec_per_block, groups = ECC_M[version]

    blocks: list[list[int]] = []
    position = 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(codewords[position:position + size])
            position += size

    ec_blocks = [rs_encode(block, ec_per_block) for block in blocks]

    result: list[int] = []
    for index in range(max(len(block) for block in blocks)):
        for block in blocks:
            if index < len(block):
                result.append(block[index])
    for index in range(ec_per_block):
        for block in ec_blocks:
            result.append(block[index])
    return result


# ---------------------------------------------------------------------------
# Die Matrix aufbauen
# ---------------------------------------------------------------------------
class Matrix:
    """Modulraster des Codes. True = dunkel."""

    def __init__(self, size: int):
        self.size = size
        self.modules = [[False] * size for _ in range(size)]
        # Reservierte Felder (Suchmuster, Format, Timing) duerfen weder
        # Daten aufnehmen noch maskiert werden.
        self.reserved = [[False] * size for _ in range(size)]

    def set(self, row: int, col: int, dark: bool, reserve: bool = True) -> None:
        self.modules[row][col] = dark
        if reserve:
            self.reserved[row][col] = True

    def __getitem__(self, row):
        return self.modules[row]

    def to_text(self, dark: str = "##", light: str = "  ") -> str:
        return "\n".join(
            "".join(dark if cell else light for cell in row) for row in self.modules
        )


def _place_finder(matrix: Matrix, row: int, col: int) -> None:
    """Suchmuster (7x7) samt Trennlinie ringsum."""
    for r in range(-1, 8):
        for c in range(-1, 8):
            rr, cc = row + r, col + c
            if not (0 <= rr < matrix.size and 0 <= cc < matrix.size):
                continue
            inside = 0 <= r <= 6 and 0 <= c <= 6
            dark = inside and (
                r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)
            )
            matrix.set(rr, cc, dark)


def _place_alignment(matrix: Matrix, version: int) -> None:
    centers = ALIGNMENT_CENTERS[version]
    last = matrix.size - 1
    for row in centers:
        for col in centers:
            # Die drei Ecken sind schon von den Suchmustern belegt.
            if (row, col) in ((6, 6), (6, last - 6), (last - 6, 6)):
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    dark = max(abs(r), abs(c)) != 1
                    matrix.set(row + r, col + c, dark)


def _place_timing(matrix: Matrix) -> None:
    for i in range(8, matrix.size - 8):
        dark = i % 2 == 0
        matrix.set(6, i, dark)
        matrix.set(i, 6, dark)


def _reserve_format_areas(matrix: Matrix, version: int) -> None:
    size = matrix.size
    for i in range(9):
        if not matrix.reserved[8][i]:
            matrix.set(8, i, False)
        if not matrix.reserved[i][8]:
            matrix.set(i, 8, False)
    for i in range(8):
        matrix.set(8, size - 1 - i, False)
        matrix.set(size - 1 - i, 8, False)
    # Das eine Modul, das laut Norm immer dunkel ist.
    matrix.set(size - 8, 8, True)

    if version >= 7:
        for i in range(18):
            row, col = i // 3, i % 3
            matrix.set(size - 11 + col, row, False)
            matrix.set(row, size - 11 + col, False)


def _bch_format(value: int) -> int:
    """10 Pruefbits fuer das 5-Bit-Formatfeld."""
    remainder = value << 10
    for shift in range(4, -1, -1):
        if remainder & (1 << (shift + 10)):
            remainder ^= 0b10100110111 << shift
    return ((value << 10) | remainder) ^ 0b101010000010010


def _bch_version(version: int) -> int:
    """12 Pruefbits fuer die 6-Bit-Versionsangabe (erst ab Version 7 noetig)."""
    remainder = version << 12
    for shift in range(5, -1, -1):
        if remainder & (1 << (shift + 12)):
            remainder ^= 0b1111100100101 << shift
    return (version << 12) | remainder


def _place_format(matrix: Matrix, ecc: str, mask: int) -> None:
    bits = _bch_format((ECC_INDICATOR[ecc] << 3) | mask)
    size = matrix.size
    for i in range(15):
        # Die Norm legt das hoechstwertige Bit zuerst ab (also bei (8,0)),
        # nicht das niederwertigste - andersherum lesen Scanner nur Unsinn.
        dark = bool((bits >> (14 - i)) & 1)
        # Erste Kopie um das linke obere Suchmuster.
        if i < 6:
            matrix.set(8, i, dark)
        elif i == 6:
            matrix.set(8, 7, dark)
        elif i == 7:
            matrix.set(8, 8, dark)
        elif i == 8:
            matrix.set(7, 8, dark)
        else:
            matrix.set(14 - i, 8, dark)
        # Zweite Kopie, verteilt auf die beiden anderen Ecken. Nur sieben Bits
        # gehen nach unten - das achte Feld von unten ist das Modul, das laut
        # Norm immer dunkel bleibt.
        if i < 7:
            matrix.set(size - 1 - i, 8, dark)
        else:
            matrix.set(8, size - 15 + i, dark)


def _place_version(matrix: Matrix, version: int) -> None:
    if version < 7:
        return
    bits = _bch_version(version)
    size = matrix.size
    for i in range(18):
        dark = bool((bits >> i) & 1)
        row, col = i // 3, i % 3
        matrix.set(size - 11 + col, row, dark)
        matrix.set(row, size - 11 + col, dark)


MASK_RULES = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
)


def _place_data(matrix: Matrix, codewords: list[int]) -> list[tuple[int, int]]:
    """
    Datenbits im Zickzack von rechts unten nach oben einsetzen.

    Gibt die belegten Felder in Reihenfolge zurueck - das brauchen die Tests,
    um die Daten wieder herauszulesen.
    """
    bits = [(byte >> shift) & 1 for byte in codewords for shift in range(7, -1, -1)]
    size = matrix.size
    positions: list[tuple[int, int]] = []
    index = 0
    upward = True

    col = size - 1
    while col > 0:
        if col == 6:  # senkrechtes Timing-Muster ueberspringen
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for offset in (0, 1):
                current = col - offset
                if matrix.reserved[row][current]:
                    continue
                bit = bits[index] if index < len(bits) else 0
                matrix.set(row, current, bool(bit), reserve=False)
                positions.append((row, current))
                index += 1
        upward = not upward
        col -= 2
    return positions


def _apply_mask(matrix: Matrix, mask: int) -> None:
    rule = MASK_RULES[mask]
    for row in range(matrix.size):
        for col in range(matrix.size):
            if not matrix.reserved[row][col] and rule(row, col):
                matrix.modules[row][col] = not matrix.modules[row][col]


def _penalty(matrix: Matrix) -> int:
    """Bewertung nach den vier Regeln der Norm - kleiner ist besser."""
    size = matrix.size
    grid = matrix.modules
    score = 0

    # Regel 1: lange Reihen gleicher Farbe.
    for line in list(grid) + [list(column) for column in zip(*grid)]:
        run_value, run_length = line[0], 1
        for cell in line[1:]:
            if cell == run_value:
                run_length += 1
            else:
                if run_length >= 5:
                    score += 3 + (run_length - 5)
                run_value, run_length = cell, 1
        if run_length >= 5:
            score += 3 + (run_length - 5)

    # Regel 2: gleichfarbige 2x2-Bloecke.
    for row in range(size - 1):
        for col in range(size - 1):
            block = (grid[row][col], grid[row][col + 1],
                     grid[row + 1][col], grid[row + 1][col + 1])
            if all(block) or not any(block):
                score += 3

    # Regel 3: Muster, das dem Suchmuster aehnelt (wuerde Scanner verwirren).
    needles = (
        [True, False, True, True, True, False, True, False, False, False, False],
        [False, False, False, False, True, False, True, True, True, False, True],
    )
    for line in list(grid) + [list(column) for column in zip(*grid)]:
        for start in range(size - 10):
            window = line[start:start + 11]
            if window == needles[0] or window == needles[1]:
                score += 40

    # Regel 4: Abweichung vom halb-hell-halb-dunkel-Verhaeltnis.
    dark = sum(sum(1 for cell in row if cell) for row in grid)
    percent = dark * 100 // (size * size)
    score += 10 * (abs(percent - 50) // 5)
    return score


def build_with_mask(text: str, mask: int, ecc: str = "M") -> Matrix:
    """Eine Matrix mit fest vorgegebener Maske - fuer Tests und Vergleiche."""
    payload = text.encode("utf-8")
    version = choose_version(payload)
    size = 17 + 4 * version
    codewords = interleave(build_data_codewords(payload, version), version)

    matrix = Matrix(size)
    _place_finder(matrix, 0, 0)
    _place_finder(matrix, 0, size - 7)
    _place_finder(matrix, size - 7, 0)
    _place_alignment(matrix, version)
    _place_timing(matrix)
    _reserve_format_areas(matrix, version)
    _place_data(matrix, codewords)
    _apply_mask(matrix, mask)
    _place_format(matrix, ecc, mask)
    _place_version(matrix, version)
    return matrix


def make_matrix(text: str, ecc: str = "M") -> Matrix:
    """Text als QR-Modulraster. True = dunkles Modul."""
    if ecc != "M":
        raise ValueError("Es ist nur die Fehlerkorrektur-Stufe M umgesetzt")
    payload = text.encode("utf-8")
    version = choose_version(payload)
    size = 17 + 4 * version

    codewords = interleave(build_data_codewords(payload, version), version)

    best = None
    for mask in range(8):
        matrix = Matrix(size)
        _place_finder(matrix, 0, 0)
        _place_finder(matrix, 0, size - 7)
        _place_finder(matrix, size - 7, 0)
        _place_alignment(matrix, version)
        _place_timing(matrix)
        _reserve_format_areas(matrix, version)
        _place_data(matrix, codewords)
        _apply_mask(matrix, mask)
        _place_format(matrix, ecc, mask)
        _place_version(matrix, version)

        score = _penalty(matrix)
        if best is None or score < best[0]:
            best = (score, matrix)
    return best[1]


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------
def to_svg(matrix: Matrix, quiet_zone: int = 4, dark: str = "#0a0705",
           light: str = "#ffe8d2") -> str:
    """
    Als SVG mit Modulen als Koordinateneinheit - die Groesse bestimmt spaeter
    das CSS. Der helle Rand ("quiet zone") gehoert dazu, ohne ihn erkennen
    viele Scanner den Code nicht.
    """
    span = matrix.size + 2 * quiet_zone
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {span} {span}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR-Code">',
        f'<rect width="{span}" height="{span}" fill="{light}"/>',
        f'<g fill="{dark}">',
    ]
    for row in range(matrix.size):
        col = 0
        while col < matrix.size:
            if not matrix.modules[row][col]:
                col += 1
                continue
            # Waagerecht zusammenhaengende Module zu einem Rechteck bundeln -
            # das spart bei einem 57er-Code gut die Haelfte der Elemente.
            start = col
            while col < matrix.size and matrix.modules[row][col]:
                col += 1
            parts.append(
                f'<rect x="{start + quiet_zone}" y="{row + quiet_zone}" '
                f'width="{col - start}" height="1"/>'
            )
    parts.append("</g></svg>")
    return "".join(parts)


def svg(text: str, **kwargs) -> str:
    """Bequemer Einzeiler: Text rein, SVG raus."""
    return to_svg(make_matrix(text), **kwargs)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_text = "--text" in args
    if as_text:
        args.remove("--text")
    if len(args) != 1:
        sys.stderr.write("Aufruf: python3 qr.py [--text] <inhalt>\n")
        return 1
    matrix = make_matrix(args[0])
    print(matrix.to_text() if as_text else to_svg(matrix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
