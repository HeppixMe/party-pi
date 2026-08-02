"""
Tests fuer den QR-Encoder.

Die Korrektheit wurde einmalig gegen einen echten Decoder (jsQR im Browser)
und einen etablierten Encoder abgesichert. Damit spaetere Aenderungen das
nicht unbemerkt zerstoeren, haelt dieser Test die fertige Matrix eines festen
Textes als Vergleichsbild fest - dazu die Bausteine einzeln.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import qr  # noqa: E402

# Fertiger Code fuer "http://pi/mitmachen" (Version 2, Stufe M, selbst gewaehlte
# Maske). Modulgenau geprueft gegen einen unabhaengigen Encoder.
REFERENCE_TEXT = "http://pi/mitmachen"
REFERENCE_MATRIX = [
    '#######..#.##...#.#######',
    '#.....#..#....##..#.....#',
    '#.###.#.#.....##..#.###.#',
    '#.###.#.#..####...#.###.#',
    '#.###.#.#..#.#..#.#.###.#',
    '#.....#.#...#####.#.....#',
    '#######.#.#.#.#.#.#######',
    '........#................',
    '#.#####..###...#..#####..',
    '..#..#.#...###..#......#.',
    '.##.###.#.#..#####.#.#.##',
    '##.#.#.#..#..#.#.#..#...#',
    '.#..###.#...#.##.##.#.###',
    '##.#.#...#......#..#.#.#.',
    '#.#.#.###..##..###...#.##',
    '#..##...##.#..#####..#..#',
    '#.######.###....#####.#..',
    '........#...#####...###..',
    '#######...#..##.#.#.#####',
    '#.....#.#.#.##..#...##.#.',
    '#.###.#.###.#.#########.#',
    '#.###.#.#......#.####.###',
    '#.###.#.#.###......#..#.#',
    '#.....#..#.#..#.#..###..#',
    '#######.#.##.....########',
]


def as_text(matrix):
    return [
        "".join("#" if cell else "." for cell in row) for row in matrix.modules
    ]


class GaloisFieldTest(unittest.TestCase):
    def test_log_and_exp_are_inverse(self):
        for value in range(1, 256):
            self.assertEqual(qr.GF_EXP[qr.GF_LOG[value]], value)

    def test_multiplication_with_zero(self):
        self.assertEqual(qr.gf_mul(0, 123), 0)
        self.assertEqual(qr.gf_mul(123, 0), 0)

    def test_multiplication_is_commutative_and_has_unit(self):
        for a in (1, 7, 128, 255):
            for b in (1, 3, 200, 254):
                self.assertEqual(qr.gf_mul(a, b), qr.gf_mul(b, a))
            self.assertEqual(qr.gf_mul(a, 1), a)

    def test_generator_polynomial_has_expected_degree(self):
        for degree in (10, 16, 22, 26):
            self.assertEqual(len(qr.rs_generator(degree)), degree + 1)
            self.assertEqual(qr.rs_generator(degree)[0], 1)


class ReedSolomonTest(unittest.TestCase):
    def syndromes(self, codeword, count):
        """Bei einem gueltigen Codewort muessen alle Syndrome null sein."""
        result = []
        for i in range(count):
            value = 0
            for position, byte in enumerate(codeword):
                exponent = (i * (len(codeword) - 1 - position)) % 255
                value ^= qr.gf_mul(byte, qr.GF_EXP[exponent])
            result.append(value)
        return result

    def test_encoded_block_is_a_valid_codeword(self):
        for ec_count in (10, 16, 18, 22, 26):
            data = [(i * 7 + 3) % 256 for i in range(20)]
            codeword = data + qr.rs_encode(data, ec_count)
            self.assertEqual(self.syndromes(codeword, ec_count), [0] * ec_count)

    def test_single_error_is_detected(self):
        data = list(range(20))
        codeword = data + qr.rs_encode(data, 16)
        codeword[5] ^= 0xFF
        self.assertNotEqual(self.syndromes(codeword, 16), [0] * 16)

    def test_correct_number_of_check_bytes(self):
        self.assertEqual(len(qr.rs_encode([1, 2, 3], 10), ), 10)
        self.assertEqual(len(qr.rs_encode([1, 2, 3], 26)), 26)


class CapacityTest(unittest.TestCase):
    def test_known_capacities_for_level_m(self):
        # Werte aus der Norm fuer den Byte-Modus, Stufe M.
        expected = {1: 14, 2: 26, 3: 42, 4: 62, 5: 84,
                    6: 106, 7: 122, 8: 152, 9: 180, 10: 213}
        for version, capacity in expected.items():
            self.assertEqual(qr.capacity_bytes(version), capacity, f"Version {version}")

    def test_block_layout_matches_total_codewords(self):
        for version, (ec_per_block, groups) in qr.ECC_M.items():
            blocks = sum(count for count, _size in groups)
            data = sum(count * size for count, size in groups)
            self.assertEqual(data + blocks * ec_per_block, qr.TOTAL_CODEWORDS[version],
                             f"Version {version}")

    def test_smallest_fitting_version_is_chosen(self):
        self.assertEqual(qr.choose_version(b"x" * 14), 1)
        self.assertEqual(qr.choose_version(b"x" * 15), 2)
        self.assertEqual(qr.choose_version(b"x" * 26), 2)
        self.assertEqual(qr.choose_version(b"x" * 27), 3)

    def test_too_long_is_rejected_with_a_clear_message(self):
        with self.assertRaises(ValueError) as caught:
            qr.choose_version(b"x" * 500)
        self.assertIn("Version", str(caught.exception))


class CodewordTest(unittest.TestCase):
    def test_header_holds_mode_and_length(self):
        codewords = qr.build_data_codewords(b"ABC", 1)
        # 0100 (Byte-Modus) + 8 Bit Laenge = 0x40 | 0, dann 0x03 + Daten
        self.assertEqual(codewords[0], 0b01000000)
        self.assertEqual(codewords[1], 0b00110100)  # Laenge 3, dann 'A' beginnt

    def test_block_is_filled_to_full_length(self):
        for version in qr.ECC_M:
            _ec, groups = qr.ECC_M[version]
            expected = sum(count * size for count, size in groups)
            self.assertEqual(len(qr.build_data_codewords(b"kurz", version)), expected)

    def test_padding_alternates_between_the_two_standard_bytes(self):
        codewords = qr.build_data_codewords(b"A", 2)
        # 4 Bit Modus + 8 Bit Laenge + 8 Bit Nutzdaten + 4 Bit Abschluss = 3 Bytes,
        # danach beginnt die Fuellfolge mit 0xEC und wechselt sich ab.
        padding = codewords[3:]
        self.assertEqual(padding[:4], [0xEC, 0x11, 0xEC, 0x11])
        self.assertEqual(padding, [qr.PAD_BYTES[i % 2] for i in range(len(padding))])

    def test_interleaving_keeps_every_codeword(self):
        for version in qr.ECC_M:
            data = qr.build_data_codewords(b"testdaten", version)
            stream = qr.interleave(data, version)
            self.assertEqual(len(stream), qr.TOTAL_CODEWORDS[version], f"Version {version}")
            # Alle Datencodewoerter tauchen wieder auf (nur in anderer Reihenfolge).
            for byte in set(data):
                self.assertGreaterEqual(stream.count(byte), 1)


class StructureTest(unittest.TestCase):
    def setUp(self):
        self.matrix = qr.make_matrix(REFERENCE_TEXT)

    def test_size_follows_the_version(self):
        self.assertEqual(self.matrix.size, 25)  # Version 2

    def test_finder_patterns_in_three_corners(self):
        size = self.matrix.size
        for row0, col0 in ((0, 0), (0, size - 7), (size - 7, 0)):
            for r in range(7):
                for c in range(7):
                    expected = r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)
                    self.assertEqual(self.matrix.modules[row0 + r][col0 + c], expected,
                                     f"Suchmuster bei {row0},{col0} Feld {r},{c}")

    def test_timing_patterns_alternate(self):
        for i in range(8, self.matrix.size - 8):
            self.assertEqual(self.matrix.modules[6][i], i % 2 == 0)
            self.assertEqual(self.matrix.modules[i][6], i % 2 == 0)

    def test_mandatory_dark_module(self):
        self.assertTrue(self.matrix.modules[self.matrix.size - 8][8])

    def test_format_information_reads_back(self):
        size = self.matrix.size
        bits = 0
        for i in range(15):
            if i < 6:
                dark = self.matrix.modules[8][i]
            elif i == 6:
                dark = self.matrix.modules[8][7]
            elif i == 7:
                dark = self.matrix.modules[8][8]
            elif i == 8:
                dark = self.matrix.modules[7][8]
            else:
                dark = self.matrix.modules[14 - i][8]
            if dark:
                bits |= 1 << (14 - i)

        unmasked = bits ^ 0b101010000010010
        self.assertEqual(unmasked >> 13, qr.ECC_INDICATOR["M"])
        mask = (unmasked >> 10) & 0b111
        self.assertEqual(qr._bch_format((qr.ECC_INDICATOR["M"] << 3) | mask), bits)

    def test_both_format_copies_are_identical(self):
        size = self.matrix.size
        first, second = [], []
        for i in range(15):
            if i < 6:
                first.append(self.matrix.modules[8][i])
            elif i == 6:
                first.append(self.matrix.modules[8][7])
            elif i == 7:
                first.append(self.matrix.modules[8][8])
            elif i == 8:
                first.append(self.matrix.modules[7][8])
            else:
                first.append(self.matrix.modules[14 - i][8])
            if i < 7:
                second.append(self.matrix.modules[size - 1 - i][8])
            else:
                second.append(self.matrix.modules[8][size - 15 + i])
        self.assertEqual(first, second)

    def test_data_modules_read_back_unchanged(self):
        """Ohne Maske muessen die eingesetzten Bits wieder herauskommen."""
        version = 2
        stream = qr.interleave(qr.build_data_codewords(REFERENCE_TEXT.encode(), version), version)
        size = 17 + 4 * version
        matrix = qr.Matrix(size)
        qr._place_finder(matrix, 0, 0)
        qr._place_finder(matrix, 0, size - 7)
        qr._place_finder(matrix, size - 7, 0)
        qr._place_alignment(matrix, version)
        qr._place_timing(matrix)
        qr._reserve_format_areas(matrix, version)
        positions = qr._place_data(matrix, stream)

        bits = [1 if matrix.modules[r][c] else 0 for r, c in positions]
        read_back = [int("".join(map(str, bits[i:i + 8])), 2)
                     for i in range(0, len(stream) * 8, 8)]
        self.assertEqual(read_back, stream)

    def test_alignment_patterns_do_not_overwrite_the_finders(self):
        # Version 7 hat drei Ausrichtungsreihen - die drei Ecken bleiben frei.
        matrix = qr.make_matrix("x" * 120)
        size = matrix.size
        for row0, col0 in ((0, 0), (0, size - 7), (size - 7, 0)):
            self.assertTrue(matrix.modules[row0 + 3][col0 + 3], "Mittelpunkt des Suchmusters")


class MaskTest(unittest.TestCase):
    def test_all_eight_rules_differ(self):
        seen = set()
        for rule in qr.MASK_RULES:
            pattern = tuple(rule(r, c) for r in range(8) for c in range(8))
            seen.add(pattern)
        self.assertEqual(len(seen), 8)

    def test_mask_leaves_function_patterns_untouched(self):
        matrix = qr.build_with_mask(REFERENCE_TEXT, 0)
        # Suchmuster muessen trotz Maske unveraendert dastehen.
        self.assertTrue(matrix.modules[0][0])
        self.assertFalse(matrix.modules[1][1])
        self.assertTrue(matrix.modules[3][3])

    def test_penalty_prefers_a_mixed_pattern(self):
        size = 21
        uniform = qr.Matrix(size)  # alles hell -> Regel 1, 2 und 4 schlagen zu
        mixed = qr.Matrix(size)
        for r in range(size):
            for c in range(size):
                mixed.modules[r][c] = (r + c) % 2 == 0
        self.assertGreater(qr._penalty(uniform), qr._penalty(mixed))

    def test_chosen_mask_is_the_cheapest(self):
        best = min(qr._penalty(qr.build_with_mask(REFERENCE_TEXT, m)) for m in range(8))
        self.assertEqual(qr._penalty(qr.make_matrix(REFERENCE_TEXT)), best)


class ReferenceMatrixTest(unittest.TestCase):
    """
    Vergleich mit einem festgehaltenen, unabhaengig geprueften Ergebnis.
    Schlaegt dieser Test fehl, hat sich die Ausgabe geaendert - dann gehoert
    sie erneut mit einem echten Scanner geprueft, bevor der Wert hier
    angepasst wird.
    """

    def test_matches_the_known_good_output(self):
        self.assertEqual(as_text(qr.make_matrix(REFERENCE_TEXT)), REFERENCE_MATRIX)


class SvgTest(unittest.TestCase):
    def setUp(self):
        self.matrix = qr.make_matrix(REFERENCE_TEXT)
        self.svg = qr.to_svg(self.matrix)

    def test_is_well_formed_svg(self):
        self.assertTrue(self.svg.startswith("<svg "))
        self.assertTrue(self.svg.endswith("</svg>"))
        self.assertEqual(self.svg.count("<svg"), 1)

    def test_quiet_zone_is_included(self):
        # Ohne den hellen Rand erkennen viele Scanner den Code nicht.
        span = self.matrix.size + 8
        self.assertIn(f'viewBox="0 0 {span} {span}"', self.svg)

    def test_no_size_attribute_so_css_decides(self):
        self.assertNotIn("width=\"%d\"" % (self.matrix.size + 8), self.svg.split(">")[0])

    def test_dark_modules_become_rectangles(self):
        self.assertGreater(self.svg.count("<rect"), 10)

    def test_convenience_wrapper(self):
        self.assertEqual(qr.svg(REFERENCE_TEXT), self.svg)


class RoundTripTest(unittest.TestCase):
    """Verschiedene Laengen und Zeichen erzeugen gueltige, passende Codes."""

    def test_versions_grow_with_the_payload(self):
        sizes = []
        for length in (10, 30, 60, 100, 150, 200):
            matrix = qr.make_matrix("x" * length)
            sizes.append(matrix.size)
        self.assertEqual(sizes, sorted(sizes))
        self.assertEqual(len(set(sizes)), len(sizes))

    def test_umlauts_are_encoded_as_utf8(self):
        matrix = qr.make_matrix("Straße")
        self.assertEqual(matrix.size, 21)  # passt noch in Version 1

    def test_empty_text_still_produces_a_code(self):
        self.assertEqual(qr.make_matrix("").size, 21)

    def test_only_level_m_is_supported(self):
        with self.assertRaises(ValueError):
            qr.make_matrix("test", ecc="H")


if __name__ == "__main__":
    unittest.main()
