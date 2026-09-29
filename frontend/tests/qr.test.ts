import { createHash } from "node:crypto";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { QrCode, qrPath } from "@/components/admin/QrCode";
import {
  encodeQr,
  formatBits,
  gfExp,
  gfLog,
  gfMultiply,
  penalty,
  rsRemainder,
  versionBits,
} from "@/lib/qr";
import type { QrLevel, QrMatrix } from "@/lib/qr";

// A real set-up link with a fake key: JBSWY3DPEHPK3PXP is the well-known
// example key from authenticator documentation, not anyone's secret.
const LINK =
  "otpauth://totp/Kaagaz:tushar%40monoedge.in?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP" +
  "&issuer=Kaagaz&algorithm=SHA1&digits=6&period=30";

const versionOf = (matrix: QrMatrix) => (matrix.size - 17) / 4;

/** A rectangle of the matrix as rows of "0" and "1". */
function block(matrix: QrMatrix, top: number, left: number, height: number, width: number) {
  const rows: string[] = [];
  for (let row = top; row < top + height; row++) {
    const start = row * matrix.size + left;
    rows.push(Array.from(matrix.modules.subarray(start, start + width)).join(""));
  }
  return rows;
}

/** The modules at `cells`, first cell as the highest bit. */
function bitsAt(matrix: QrMatrix, cells: [number, number][]): number {
  return cells.reduce((bits, [row, col]) => (bits << 1) | (matrix.modules[row * matrix.size + col] ?? 0), 0);
}

// The first copy of the format bits, highest bit first: along row 8 from the
// left, then up column 8.
const FORMAT_COPY_1: [number, number][] = [
  [8, 0], [8, 1], [8, 2], [8, 3], [8, 4], [8, 5], [8, 7], [8, 8],
  [7, 8], [5, 8], [4, 8], [3, 8], [2, 8], [1, 8], [0, 8],
];

/** The mask the format bits name, or -1 if they name none. */
function maskOf(matrix: QrMatrix, level: QrLevel): number {
  const bits = bitsAt(matrix, FORMAT_COPY_1);
  return [0, 1, 2, 3, 4, 5, 6, 7].findIndex((mask) => formatBits(level, mask) === bits);
}

const sha256Of = (matrix: QrMatrix) => createHash("sha256").update(matrix.modules).digest("hex");

/** A crafted matrix from rows of "0" and "1". */
const matrixOf = (rows: string[]): QrMatrix => ({
  size: rows.length,
  modules: Uint8Array.from(rows.join(""), (bit) => Number(bit)),
});

/** An n × n all-light matrix. */
const blank = (n: number) => matrixOf(Array.from({ length: n }, () => "0".repeat(n)));

describe("GF(256)", () => {
  it("has exp and log tables that undo each other", () => {
    for (let power = 0; power < 255; power++) expect(gfLog(gfExp(power))).toBe(power);
    for (let value = 1; value < 256; value++) expect(gfExp(gfLog(value))).toBe(value);
    expect(gfExp(8)).toBe(0x1d); // α^8 = x^4 + x^3 + x^2 + 1, from 0x11D
    expect(gfExp(255)).toBe(1);
  });

  it("multiplies like the slow shift-and-XOR method, for every pair", () => {
    const slow = (a: number, b: number) => {
      let product = 0;
      for (; b > 0; b >>= 1) {
        if (b & 1) product ^= a;
        a = a & 0x80 ? (a << 1) ^ 0x11d : a << 1;
      }
      return product;
    };
    let wrong = 0;
    for (let a = 0; a < 256; a++) {
      for (let b = 0; b < 256; b++) if (gfMultiply(a, b) !== slow(a, b)) wrong++;
    }
    expect(wrong).toBe(0);
  });
});

describe("Reed-Solomon", () => {
  it("gives the published error correction for HELLO WORLD at 1-M", () => {
    // thonky.com QR code tutorial, "Error Correction Coding": the 16 data
    // codewords of "HELLO WORLD" (alphanumeric mode) at 1-M, and their 10.
    const data = Uint8Array.of(32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17);
    expect(Array.from(rsRemainder(data, 10))).toEqual([196, 35, 39, 119, 235, 215, 231, 226, 93, 23]);
  });

  it("gives the published error correction for 01234567 at 1-M", () => {
    // The worked encoding example in ISO/IEC 18004 ("01234567", numeric mode, 1-M).
    const data = Uint8Array.of(16, 32, 12, 86, 97, 128, 236, 17, 236, 17, 236, 17, 236, 17, 236, 17);
    expect(Array.from(rsRemainder(data, 10))).toEqual([165, 36, 212, 193, 237, 54, 199, 135, 44, 85]);
  });
});

describe("format and version bits", () => {
  it("matches the published format strings", () => {
    // thonky.com QR code tutorial, format information table.
    const known: [QrLevel, number, string][] = [
      ["L", 0, "111011111000100"],
      ["L", 4, "110011000101111"],
      ["M", 0, "101010000010010"],
      ["M", 5, "100000011001110"],
      ["M", 7, "100101010100000"],
      ["Q", 0, "011010101011111"],
      ["H", 0, "001011010001001"],
    ];
    for (const [level, mask, bits] of known) {
      expect(formatBits(level, mask).toString(2).padStart(15, "0"), `${level}/${mask}`).toBe(bits);
    }
  });

  it("matches the published version strings", () => {
    expect(versionBits(7).toString(2).padStart(18, "0")).toBe("000111110010010100");
    expect(versionBits(8).toString(2).padStart(18, "0")).toBe("001000010110111100");
    expect(versionBits(40).toString(2).padStart(18, "0")).toBe("101000110001101001");
  });
});

describe("encodeQr", () => {
  it("picks the smallest version that holds the bytes", () => {
    // [level, bytes, version]. 1-L holds 17 bytes and 1-M holds 14. From
    // version 10 the byte count takes 16 bits instead of 8.
    const cases: [QrLevel, number, number][] = [
      ["L", 17, 1],
      ["L", 18, 2],
      ["M", 14, 1],
      ["M", 15, 2],
      ["M", 180, 9],
      ["M", 181, 10],
      ["Q", 86, 7],
      ["Q", 87, 8],
      ["H", 1273, 40],
      ["L", 2953, 40],
    ];
    for (const [level, bytes, version] of cases) {
      expect(versionOf(encodeQr("a".repeat(bytes), level)), `${bytes} bytes at ${level}`).toBe(version);
    }
  });

  it("counts UTF-8 bytes, not characters", () => {
    expect(versionOf(encodeQr("न".repeat(5), "L"))).toBe(1); // 15 bytes
    expect(versionOf(encodeQr("न".repeat(6), "L"))).toBe(2); // 18 bytes
  });

  it("throws when the text does not fit even in version 40, without echoing it", () => {
    const secret = "JBSWY3DP".repeat(400); // 3200 bytes
    expect(() => encodeQr(secret, "L")).toThrow("3200 bytes, the most is 2953");
    expect(() => encodeQr("a".repeat(1274), "H")).toThrow("the most is 1273");
    let message = "";
    try {
      encodeQr(secret, "L");
    } catch (error) {
      message = (error as Error).message;
    }
    expect(message).not.toContain("JBSW");
  });

  it("is a square of 0s and 1s, 4 × version + 17 a side", () => {
    for (const bytes of [1, 30, 120, 400, 1500, 2953]) {
      const matrix = encodeQr("x".repeat(bytes), "L");
      expect(matrix.size).toBe(4 * versionOf(matrix) + 17);
      expect(Number.isInteger(versionOf(matrix))).toBe(true);
      expect(matrix.modules).toHaveLength(matrix.size * matrix.size);
      expect(matrix.modules.every((value) => value === 0 || value === 1)).toBe(true);
    }
  });

  it("draws the finders with their separators, the timing lines and the dark module", () => {
    for (const text of ["HELLO", LINK, "x".repeat(2953)]) {
      const matrix = encodeQr(text, "L");
      const n = matrix.size;
      const finder = ["1111111", "1000001", "1011101", "1011101", "1011101", "1000001", "1111111"];
      expect(block(matrix, 0, 0, 8, 8)).toEqual([...finder.map((row) => `${row}0`), "00000000"]);
      expect(block(matrix, 0, n - 8, 8, 8)).toEqual([...finder.map((row) => `0${row}`), "00000000"]);
      expect(block(matrix, n - 8, 0, 8, 8)).toEqual(["00000000", ...finder.map((row) => `${row}0`)]);
      const timing = "10".repeat(n).slice(0, n - 16); // dark first, between the finders
      expect(block(matrix, 6, 8, 1, n - 16)).toEqual([timing]);
      expect(block(matrix, 8, 6, n - 16, 1).join("")).toBe(timing);
      expect(matrix.modules[(n - 8) * n + 8]).toBe(1); // row 4 × version + 9, column 8
    }
  });

  it("draws the alignment patterns where the standard puts them", () => {
    const alignment = ["11111", "10001", "10101", "10001", "11111"];
    const version7 = encodeQr("a".repeat(150), "L"); // centres 6, 22, 38
    expect(versionOf(version7)).toBe(7);
    const centres: [number, number][] = [[6, 22], [22, 6], [22, 22], [22, 38], [38, 22], [38, 38]];
    for (const [row, col] of centres) {
      expect(block(version7, row - 2, col - 2, 5, 5), `${row},${col}`).toEqual(alignment);
    }
    const version2 = encodeQr("a".repeat(20), "L"); // one pattern, at 18, 18
    expect(versionOf(version2)).toBe(2);
    expect(block(version2, 16, 16, 5, 5)).toEqual(alignment);
  });

  it("writes the format bits twice, and from version 7 the version bits twice", () => {
    for (const [text, level] of [["HELLO", "L"], [LINK, "M"], [LINK, "H"]] as [string, QrLevel][]) {
      const matrix = encodeQr(text, level);
      const n = matrix.size;
      // Copy 2, highest bit first: up column 8 from the bottom, then along row
      // 8 to the right edge.
      const second = bitsAt(matrix, [
        ...Array.from({ length: 7 }, (_, i): [number, number] => [n - 1 - i, 8]),
        ...Array.from({ length: 8 }, (_, i): [number, number] => [8, n - 8 + i]),
      ]);
      expect(second).toBe(bitsAt(matrix, FORMAT_COPY_1));
      expect(maskOf(matrix, level)).not.toBe(-1);
    }
    const version7 = encodeQr("a".repeat(150), "L");
    const n = version7.size;
    const topRight = Array.from({ length: 18 }, (_, i): [number, number] => [
      Math.floor((17 - i) / 3),
      n - 11 + ((17 - i) % 3),
    ]);
    const bottomLeft = topRight.map(([row, col]): [number, number] => [col, row]);
    expect(bitsAt(version7, topRight)).toBe(versionBits(7));
    expect(bitsAt(version7, bottomLeft)).toBe(versionBits(7));
  });

  it("gives the same matrix every time", () => {
    expect(encodeQr(LINK, "Q")).toEqual(encodeQr(LINK, "Q"));
    expect(encodeQr("नमस्ते Kaagaz")).toEqual(encodeQr("नमस्ते Kaagaz", "M"));
  });

  it("encodes a real set-up link to the matrix a QR reader decoded", () => {
    // Checked outside the repo: this matrix, drawn as a PNG, decodes back to
    // LINK with OpenCV's QR readers, and every block reads back with 0 errors.
    const matrix = encodeQr(LINK, "M");
    expect(matrix.size).toBe(49); // version 8
    expect(sha256Of(matrix)).toBe("39882f34140c76a6dd4105b2c5917b7ad3c1e54d129e480f30789a8770666233");
  });

  it("uses each of the 8 masks for some text, each matrix one a QR reader decoded", () => {
    // Checked outside the repo like the link above: each decodes back with
    // OpenCV's QR readers and reads back with 0 errors. A wrong mask formula
    // changes the matrix for that mask. [text, level, version, mask, sha256]
    const goldens: [string, QrLevel, number, number, string][] = [
      ["Kaagaz 14", "Q", 1, 0, "89efacd45ac6d700ba6ec944a71c05ef7d11d679a528ed71ce13b2a05f1d68f5"],
      ["Kaagaz 51", "H", 2, 1, "75f28c7018d6c2b5825da6e2d1ea5736490e39b9e85ef859ad87f87ee502798c"],
      ["Kaagaz 2", "Q", 1, 2, "62f601ac120b2899f8122ba62f2901eb2c8d24427e2b7edd73d5fd2ae69cb8ba"],
      ["Kaagaz 25", "M", 1, 3, "315aa101cd589ce39001f9f1518dc3270239dd7dc57703226f8ab62e2c9e6600"],
      ["Kaagaz 17", "M", 1, 4, "6f9ddb413b82c6e3c4d75e65109f19cb1ee7f28affba03fa5d9be44c977ffbc2"],
      ["Kaagaz 0", "L", 1, 5, "f0b88df0b9d87c533a41ec4cf70f5211722b02b26712570d27468e6a47325427"],
      ["Kaagaz 6", "Q", 1, 6, "2d256e4d634c47c477ffa1435988190fa4b1d5dcaf1f279c4ff1dc7c366193ec"],
      ["Kaagaz 1", "M", 1, 7, "8dfa902ff06def3fa9128a751e3314529e344a3d94d4a4222668f3e5dd1e45d4"],
    ];
    for (const [text, level, version, mask, sha256] of goldens) {
      const matrix = encodeQr(text, level);
      expect(versionOf(matrix), text).toBe(version);
      expect(maskOf(matrix, level), text).toBe(mask);
      expect(sha256Of(matrix), text).toBe(sha256);
    }
  });
});

describe("penalty", () => {
  it("scores runs (N1), 2 × 2 blocks (N2) and the dark share (N4) on light squares", () => {
    // 5 × 5: N1 10 lines × 3 = 30, N2 16 blocks × 3 = 48, N4 0% dark is 10 steps of 5% = 100.
    expect(penalty(blank(5))).toBe(178);
    // 6 × 6: N1 12 lines × (3 + 1) = 48, N2 25 blocks × 3 = 75, N4 100.
    expect(penalty(blank(6))).toBe(223);
  });

  it("gives a checkerboard no penalty", () => {
    // No runs of 5, no blocks, no finder-like run, and 13 of 25 dark (52%) is under one step.
    expect(penalty(matrixOf(["10101", "01010", "10101", "01010", "10101"]))).toBe(0);
  });

  it("scores a finder-like run (N3) once for each side with 4 light modules", () => {
    // Row 3 is 1011101, light on both sides (the quiet zone counts): N3 2 × 40 = 80.
    // N1 6 light rows × (3 + 2) + 2 light columns × 5 = 40. N2 24 blocks × 3 = 72.
    // N4 5 of 49 dark (10.2%) is 7 whole steps from half = 70.
    const bothSides = ["0000000", "0000000", "0000000", "1011101", "0000000", "0000000", "0000000"];
    expect(penalty(matrixOf(bothSides))).toBe(262);
    // Row 0 is 11011101: a dark module before the run, so only the side after counts: N3 40.
    // N1 7 light rows × 6 + 6 columns under a dark module × 5 + 2 light columns × 6 = 84.
    // N2 42 blocks × 3 = 126. N4 6 of 64 dark (9.4%) is 8 steps = 80.
    const oneSide = ["11011101", ...Array.from({ length: 7 }, () => "00000000")];
    expect(penalty(matrixOf(oneSide))).toBe(330);
  });
});

describe("QrCode", () => {
  it("merges each run of dark modules in a row into one h segment", () => {
    const matrix: QrMatrix = { size: 3, modules: Uint8Array.of(1, 1, 0, 0, 1, 1, 1, 0, 1) };
    expect(qrPath(matrix)).toBe("M4 4.5h2M5 5.5h2M4 6.5h1M6 6.5h1");
  });

  it("draws exactly the dark modules, as one short path with no trace of the text", () => {
    const matrix = encodeQr(LINK);
    const path = qrPath(matrix);
    expect(path).toMatch(/^(M\d+ \d+\.5h\d+)+$/);
    const drawn = new Uint8Array(matrix.size * matrix.size);
    let segments = 0;
    for (const [, x, y, width] of path.matchAll(/M(\d+) (\d+)\.5h(\d+)/g)) {
      const start = (Number(y) - 4) * matrix.size + Number(x) - 4;
      drawn.fill(1, start, start + Number(width));
      segments++;
    }
    expect(drawn).toEqual(matrix.modules);
    // One segment per run: count the places where a dark run starts.
    let runs = 0;
    matrix.modules.forEach((value, i) => {
      if (value === 1 && (i % matrix.size === 0 || matrix.modules[i - 1] === 0)) runs++;
    });
    expect(segments).toBe(runs);
    expect(path).not.toContain("JBSW");
  });

  it("renders one svg with one path, black on white, and never the value", () => {
    const html = renderToStaticMarkup(
      createElement(QrCode, { value: LINK, label: "QR code for your authenticator app" }),
    );
    expect(html.match(/<svg/g)).toHaveLength(1);
    expect(html.match(/<path/g)).toHaveLength(1);
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-label="QR code for your authenticator app"');
    expect(html).toContain('shape-rendering="crispEdges"');
    expect(html).toContain('stroke="#000"');
    expect(html).toContain("background:#fff");
    for (const leak of ["otpauth", "secret", "JBSW", "tushar", "href", "src=", "data-", "<title"]) {
      expect(html, leak).not.toContain(leak);
    }
  });
});
