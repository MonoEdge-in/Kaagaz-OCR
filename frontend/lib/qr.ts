// Our own QR code encoder (ISO/IEC 18004), so an admin can set up 2FA by
// scanning the set-up link with Google or Microsoft Authenticator. Byte mode
// with UTF-8 text, levels L, M, Q and H, versions 1 to 40. Pure functions, no
// DOM, so it is unit tested in node (tests/qr.test.ts). The text is often a
// secret key: it is never logged, and an error names only its length.
//
// encodeQr is the one export for the app. gfExp, gfLog, gfMultiply,
// rsRemainder, formatBits, versionBits and penalty are exported for the tests.
//
// Typed array reads below end in `?? 0`. The index is always in range, but the
// type checker cannot know that (noUncheckedIndexedAccess).

export type QrLevel = "L" | "M" | "Q" | "H";

/** A square of modules, row by row from the top left. 1 is dark, 0 is light. */
export interface QrMatrix {
  size: number; // modules per side, 4 × version + 17, quiet zone not included
  modules: Uint8Array; // size × size
}

// --- The standard's tables ---------------------------------------------------

const LEVEL_ROW: Record<QrLevel, number> = { L: 0, M: 1, Q: 2, H: 3 };
// The 2 bits each level puts in the format information.
const LEVEL_BITS: Record<QrLevel, number> = { L: 1, M: 0, Q: 3, H: 2 };

// The standard's error correction table: one row of 40 per level (L, M, Q, H),
// versions 1 to 40. The error correction codewords in each block, then the
// number of blocks in group 1 and in group 2. A group 2 block holds one data
// codeword more than a group 1 block.
const ECC_PER_BLOCK = Uint8Array.of(
  7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
  28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
  10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
  26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28,
  13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
  28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
  17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
  30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30,
);
const GROUP1_BLOCKS = Uint8Array.of(
  1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 4, 2, 4, 3, 5, 5, 1, 5, 3, 3,
  4, 2, 4, 6, 8, 10, 8, 3, 7, 5, 13, 17, 17, 13, 12, 6, 17, 4, 20, 19,
  1, 1, 1, 2, 2, 4, 4, 2, 3, 4, 1, 6, 8, 4, 5, 7, 10, 9, 3, 3,
  17, 17, 4, 6, 8, 19, 22, 3, 21, 19, 2, 10, 14, 14, 12, 6, 29, 13, 40, 18,
  1, 1, 2, 2, 2, 4, 2, 4, 4, 6, 4, 4, 8, 11, 5, 15, 1, 17, 17, 15,
  17, 7, 11, 11, 7, 28, 8, 4, 1, 15, 42, 10, 29, 44, 39, 46, 49, 48, 43, 34,
  1, 1, 2, 4, 2, 4, 4, 4, 4, 6, 3, 7, 12, 11, 11, 3, 2, 2, 9, 15,
  19, 34, 16, 30, 22, 33, 12, 11, 19, 23, 23, 19, 11, 59, 22, 2, 24, 42, 10, 20,
);
const GROUP2_BLOCKS = Uint8Array.of(
  0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 2, 0, 1, 1, 1, 5, 1, 4, 5,
  4, 7, 5, 4, 4, 2, 4, 10, 7, 10, 3, 0, 1, 6, 7, 14, 4, 18, 4, 6,
  0, 0, 0, 0, 0, 0, 0, 2, 2, 1, 4, 2, 1, 5, 5, 3, 1, 4, 11, 13,
  0, 0, 14, 14, 13, 4, 3, 23, 7, 10, 29, 23, 21, 23, 26, 34, 14, 32, 7, 31,
  0, 0, 0, 0, 2, 0, 4, 2, 4, 2, 4, 6, 4, 5, 7, 2, 15, 1, 4, 5,
  6, 16, 14, 16, 22, 6, 26, 31, 37, 25, 1, 35, 19, 7, 14, 10, 10, 14, 22, 34,
  0, 0, 0, 0, 2, 0, 1, 2, 4, 2, 8, 4, 4, 5, 7, 13, 17, 19, 16, 10,
  6, 0, 14, 2, 13, 4, 28, 31, 26, 25, 28, 35, 46, 1, 41, 64, 46, 32, 67, 61,
);

interface Blocks {
  ecc: number; // error correction codewords per block
  group1: number;
  group2: number;
  data: number; // data codewords in all blocks together
}

/** All codewords (data and error correction) that a version holds. */
function totalCodewords(version: number): number {
  const size = version * 4 + 17;
  // Every module, less the 3 finders with their separators (8 × 8 each), the
  // 2 timing lines, and the 2 copies of the format bits with the dark module.
  let modules = size * size - 3 * 64 - 2 * (size - 16) - 31;
  if (version >= 2) {
    // The 5 × 5 alignment patterns sit on a grid, less the 3 grid spots under
    // the finders. Those on row or column 6 also cover 5 timing modules each,
    // which were already taken away once.
    const count = Math.floor(version / 7) + 2;
    modules -= 25 * (count * count - 3) - 10 * (count - 2);
  }
  if (version >= 7) modules -= 36; // the 2 copies of the version bits
  return Math.floor(modules / 8); // the few modules left over stay light
}

function blocksFor(version: number, level: QrLevel): Blocks {
  const at = LEVEL_ROW[level] * 40 + version - 1;
  const ecc = ECC_PER_BLOCK[at] ?? 0;
  const group1 = GROUP1_BLOCKS[at] ?? 0;
  const group2 = GROUP2_BLOCKS[at] ?? 0;
  return { ecc, group1, group2, data: totalCodewords(version) - ecc * (group1 + group2) };
}

/** Bits in the byte count: 8 up to version 9, then 16. */
function countBits(version: number): number {
  return version < 10 ? 8 : 16;
}

// --- GF(256) and Reed-Solomon -------------------------------------------------

// Sums and products in GF(256), with the standard's polynomial
// x^8 + x^4 + x^3 + x^2 + 1 (0x11D). EXP[i] is α^i (α = 2) and LOG undoes it,
// so a multiply is 3 lookups and an add. EXP holds 2 rounds of 255, so
// EXP[LOG a + LOG b] never needs "mod 255".
const EXP = new Uint8Array(510);
const LOG = new Uint8Array(256);
for (let i = 0, x = 1; i < 255; i++) {
  EXP[i] = x;
  EXP[i + 255] = x;
  LOG[x] = i;
  x = x & 0x80 ? (x << 1) ^ 0x11d : x << 1;
}

/** α^power, for power 0 to 509. */
export function gfExp(power: number): number {
  return EXP[power] ?? 0;
}

/** The power of α that gives `value`, for value 1 to 255. */
export function gfLog(value: number): number {
  return LOG[value] ?? 0;
}

export function gfMultiply(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return EXP[(LOG[a] ?? 0) + (LOG[b] ?? 0)] ?? 0;
}

// Generator polynomials by degree (the error correction codewords per block),
// each built once.
const GENERATORS = new Map<number, Uint8Array>();

/**
 * (x + α^0)(x + α^1)…(x + α^(degree − 1)), highest power first, with its
 * leading 1 left out. (In GF(256) minus is the same as plus.)
 */
function generator(degree: number): Uint8Array {
  const known = GENERATORS.get(degree);
  if (known) return known;
  const poly = new Uint8Array(degree);
  poly[degree - 1] = 1; // start from the polynomial 1
  for (let i = 0, root = 1; i < degree; i++, root = gfMultiply(root, 2)) {
    // Multiply by (x + root). poly[degree] is past the end, so it reads as 0.
    for (let j = 0; j < degree; j++) {
      poly[j] = gfMultiply(poly[j] ?? 0, root) ^ (poly[j + 1] ?? 0);
    }
  }
  GENERATORS.set(degree, poly);
  return poly;
}

/**
 * The error correction codewords for one block: the remainder when the data,
 * moved up by `eccLength` powers of x, is divided by the generator polynomial.
 */
export function rsRemainder(data: Uint8Array, eccLength: number): Uint8Array {
  const divisor = generator(eccLength);
  const remainder = new Uint8Array(eccLength);
  for (const byte of data) {
    const factor = byte ^ (remainder[0] ?? 0);
    remainder.copyWithin(0, 1);
    remainder[eccLength - 1] = 0;
    for (let j = 0; j < eccLength; j++) {
      remainder[j] = (remainder[j] ?? 0) ^ gfMultiply(divisor[j] ?? 0, factor);
    }
  }
  return remainder;
}

// --- Format and version bits ----------------------------------------------------

/**
 * The 15 format bits: the level's 2 bits and the mask's 3 bits, then 10 bits
 * of BCH(15,5) error correction, all XORed with 0x5412 so they are never all
 * light.
 */
export function formatBits(level: QrLevel, mask: number): number {
  const data = (LEVEL_BITS[level] << 3) | mask;
  let remainder = data;
  for (let i = 0; i < 10; i++) remainder = (remainder << 1) ^ ((remainder >>> 9) * 0x537);
  return ((data << 10) | remainder) ^ 0x5412;
}

/** The 18 version bits (version 7 and up): 6 bits of version, then 12 of BCH(18,6). */
export function versionBits(version: number): number {
  let remainder = version;
  for (let i = 0; i < 12; i++) remainder = (remainder << 1) ^ ((remainder >>> 11) * 0x1f25);
  return (version << 12) | remainder;
}

// --- The codewords ---------------------------------------------------------------

/** The smallest version that holds `byteCount` bytes at `level`. */
function smallestVersion(byteCount: number, level: QrLevel): number {
  for (let version = 1; version <= 40; version++) {
    const bits = 4 + countBits(version) + byteCount * 8;
    if (bits <= blocksFor(version, level).data * 8) return version;
  }
  const most = Math.floor((blocksFor(40, level).data * 8 - 20) / 8);
  throw new Error(
    `The text is too long for a QR code at level ${level}: ${byteCount} bytes, the most is ${most}.`,
  );
}

/** Mode, byte count, the bytes, terminator and padding, as `capacity` data codewords. */
function dataCodewords(bytes: Uint8Array, version: number, capacity: number): Uint8Array {
  const out = new Uint8Array(capacity);
  let bit = 0;
  const put = (value: number, length: number) => {
    for (let i = length - 1; i >= 0; i--, bit++) {
      if ((value >>> i) & 1) out[bit >>> 3] = (out[bit >>> 3] ?? 0) | (0x80 >>> (bit & 7));
    }
  };
  put(0b0100, 4); // byte mode
  put(bytes.length, countBits(version));
  for (const byte of bytes) put(byte, 8);
  // The terminator (up to 4 zero bits) and the zeros up to a whole byte are
  // already 0 in `out`, so the pad bytes start at the next whole byte.
  const first = Math.ceil(Math.min(bit + 4, capacity * 8) / 8);
  for (let i = first; i < capacity; i++) out[i] = (i - first) % 2 === 0 ? 0xec : 0x11;
  return out;
}

/** Splits the data into blocks, adds each block's error correction, then interleaves. */
function interleave(data: Uint8Array, blocks: Blocks): Uint8Array {
  const count = blocks.group1 + blocks.group2;
  const short = Math.floor(data.length / count); // data codewords in a group 1 block
  const start = (block: number) => block * short + Math.max(0, block - blocks.group1);
  const ecc: Uint8Array[] = [];
  for (let block = 0; block < count; block++) {
    const length = block < blocks.group1 ? short : short + 1;
    ecc.push(rsRemainder(data.subarray(start(block), start(block) + length), blocks.ecc));
  }
  const out = new Uint8Array(data.length + count * blocks.ecc);
  let next = 0;
  // Data codeword i of each block in turn. Only group 2 blocks have the last one.
  for (let i = 0; i <= short; i++) {
    for (let block = 0; block < count; block++) {
      if (i === short && block < blocks.group1) continue;
      out[next++] = data[start(block) + i] ?? 0;
    }
  }
  // Then error correction codeword i of each block in turn.
  for (let i = 0; i < blocks.ecc; i++) {
    for (const codewords of ecc) out[next++] = codewords[i] ?? 0;
  }
  return out;
}

// --- The matrix ---------------------------------------------------------------------

interface Grid {
  size: number;
  modules: Uint8Array; // 1 = dark
  reserved: Uint8Array; // 1 = part of a function pattern: no data, no mask
}

function setFunction(grid: Grid, row: number, col: number, dark: boolean): void {
  const i = row * grid.size + col;
  grid.modules[i] = dark ? 1 : 0;
  grid.reserved[i] = 1;
}

/** A 7 × 7 finder centred on (row, col), with its light separator round it. */
function drawFinder(grid: Grid, row: number, col: number): void {
  for (let dy = -4; dy <= 4; dy++) {
    for (let dx = -4; dx <= 4; dx++) {
      const r = row + dy;
      const c = col + dx;
      if (r < 0 || r >= grid.size || c < 0 || c >= grid.size) continue;
      const ring = Math.max(Math.abs(dy), Math.abs(dx)); // 0 at the centre, 4 outside
      setFunction(grid, r, c, ring !== 2 && ring !== 4);
    }
  }
}

/** A 5 × 5 alignment pattern centred on (row, col). */
function drawAlignment(grid: Grid, row: number, col: number): void {
  for (let dy = -2; dy <= 2; dy++) {
    for (let dx = -2; dx <= 2; dx++) {
      setFunction(grid, row + dy, col + dx, Math.max(Math.abs(dy), Math.abs(dx)) !== 1);
    }
  }
}

/**
 * The rows (and the same columns) of the alignment pattern centres. The first
 * is always 6 and the last is size − 7. The rest are spaced back from the last
 * by one even step. This gives the standard's table of alignment positions.
 */
function alignmentCentres(version: number): number[] {
  if (version === 1) return [];
  const count = Math.floor(version / 7) + 2;
  const step = Math.floor((version * 8 + count * 3 + 5) / (count * 4 - 4)) * 2;
  const centres = [6];
  for (let i = count - 2; i >= 0; i--) centres.push(version * 4 + 10 - i * step);
  return centres;
}

/**
 * The 15 format bits, twice: round the top left finder, and split between the
 * other two finders. Also the dark module, which is always dark.
 */
function drawFormat(grid: Grid, level: QrLevel, mask: number): void {
  const bits = formatBits(level, mask);
  const bit = (i: number) => ((bits >>> i) & 1) === 1;
  const last = grid.size - 1;
  for (let i = 0; i <= 5; i++) setFunction(grid, i, 8, bit(i));
  setFunction(grid, 7, 8, bit(6));
  setFunction(grid, 8, 8, bit(7));
  setFunction(grid, 8, 7, bit(8));
  for (let i = 9; i < 15; i++) setFunction(grid, 8, 14 - i, bit(i));
  for (let i = 0; i < 8; i++) setFunction(grid, 8, last - i, bit(i));
  for (let i = 8; i < 15; i++) setFunction(grid, last - 14 + i, 8, bit(i));
  setFunction(grid, last - 7, 8, true);
}

/** The 18 version bits, twice: by the top right finder and by the bottom left one. */
function drawVersion(grid: Grid, version: number): void {
  const bits = versionBits(version);
  for (let i = 0; i < 18; i++) {
    const dark = ((bits >>> i) & 1) === 1;
    const a = grid.size - 11 + (i % 3);
    const b = Math.floor(i / 3);
    setFunction(grid, b, a, dark); // top right: 6 rows × 3 columns
    setFunction(grid, a, b, dark); // bottom left: 3 rows × 6 columns
  }
}

function drawFunctionPatterns(grid: Grid, version: number, level: QrLevel): void {
  const { size } = grid;
  // Timing patterns along row 6 and column 6, dark first. The finders and
  // alignment patterns drawn next cover the parts that cross them.
  for (let i = 0; i < size; i++) {
    setFunction(grid, 6, i, i % 2 === 0);
    setFunction(grid, i, 6, i % 2 === 0);
  }
  drawFinder(grid, 3, 3);
  drawFinder(grid, 3, size - 4);
  drawFinder(grid, size - 4, 3);
  const centres = alignmentCentres(version);
  const last = centres.length - 1;
  for (let a = 0; a <= last; a++) {
    for (let b = 0; b <= last; b++) {
      const underFinder = (a === 0 && (b === 0 || b === last)) || (a === last && b === 0);
      if (!underFinder) drawAlignment(grid, centres[a] ?? 0, centres[b] ?? 0);
    }
  }
  // Reserves the format areas now. The real mask's bits are drawn at the end.
  drawFormat(grid, level, 0);
  if (version >= 7) drawVersion(grid, version);
}

/**
 * The codewords' bits in the standard's zig-zag: 2 columns at a time from the
 * right, going up, then down, and so on, skipping column 6 (timing). Modules
 * left over (remainder bits) stay light.
 */
function placeCodewords(grid: Grid, codewords: Uint8Array): void {
  const { size, modules, reserved } = grid;
  const total = codewords.length * 8;
  let bit = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5; // column 6 is the vertical timing pattern
    const upward = ((right + 1) & 2) === 0;
    for (let step = 0; step < size; step++) {
      const row = upward ? size - 1 - step : step;
      for (let col = right; col >= right - 1; col--) {
        const i = row * size + col;
        if (reserved[i] === 1 || bit >= total) continue;
        modules[i] = ((codewords[bit >>> 3] ?? 0) >>> (7 - (bit & 7))) & 1;
        bit++;
      }
    }
  }
}

/** Whether mask pattern `mask` (0 to 7) flips the module at (row, col). */
function maskFlips(mask: number, row: number, col: number): boolean {
  switch (mask) {
    case 0:
      return (row + col) % 2 === 0;
    case 1:
      return row % 2 === 0;
    case 2:
      return col % 3 === 0;
    case 3:
      return (row + col) % 3 === 0;
    case 4:
      return (Math.floor(row / 2) + Math.floor(col / 3)) % 2 === 0;
    case 5:
      return ((row * col) % 2) + ((row * col) % 3) === 0;
    case 6:
      return (((row * col) % 2) + ((row * col) % 3)) % 2 === 0;
    default: // 7
      return (((row + col) % 2) + ((row * col) % 3)) % 2 === 0;
  }
}

/** Flips every data module the mask covers. Doing it twice undoes it. */
function applyMask(grid: Grid, mask: number): void {
  const { size, modules, reserved } = grid;
  for (let row = 0; row < size; row++) {
    for (let col = 0; col < size; col++) {
      const i = row * size + col;
      if (reserved[i] === 0 && maskFlips(mask, row, col)) modules[i] = (modules[i] ?? 0) ^ 1;
    }
  }
}

// The standard's penalty weights, rules N1 to N4.
const N1 = 3;
const N2 = 3;
const N3 = 40;
const N4 = 10;

// A finder-like run (dark, light, 3 dark, light, dark) with 4 light modules
// after it or before it, as 11 bits, the first module in the highest bit.
const FINDER_THEN_LIGHT = 0b10111010000;
const LIGHT_THEN_FINDER = 0b00001011101;

/**
 * Rules N1 and N3 along one row or column: its first index, and the step from
 * one module to the next. N1: a run of 5 or more of one colour scores 3, plus 1
 * for each module past 5. N3: each finder-like run with 4 light modules on a
 * side scores 40 for that side. Outside the symbol counts as light (it is the
 * quiet zone).
 */
function linePenalty(modules: Uint8Array, size: number, first: number, step: number): number {
  let score = 0;
  let colour = -1;
  let run = 0;
  let window = 0; // the last 11 modules, the newest in the lowest bit
  for (let i = -4; i < size + 4; i++) {
    const inside = i >= 0 && i < size;
    const dark = inside ? (modules[first + i * step] ?? 0) : 0;
    if (inside && dark === colour) {
      run++;
      if (run === 5) score += N1;
      else if (run > 5) score += 1;
    } else if (inside) {
      colour = dark;
      run = 1;
    }
    window = ((window << 1) | dark) & 0x7ff;
    if (window === FINDER_THEN_LIGHT || window === LIGHT_THEN_FINDER) score += N3;
  }
  return score;
}

/** The standard's penalty score for a masked symbol. Lower is easier to scan. */
export function penalty(matrix: QrMatrix): number {
  const { size, modules } = matrix;
  let score = 0;
  for (let line = 0; line < size; line++) {
    score += linePenalty(modules, size, line * size, 1); // row
    score += linePenalty(modules, size, line, size); // column
  }
  // N2: each 2 × 2 block of one colour.
  for (let row = 0; row < size - 1; row++) {
    for (let col = 0; col < size - 1; col++) {
      const i = row * size + col;
      const colour = modules[i];
      if (
        colour === modules[i + 1] &&
        colour === modules[i + size] &&
        colour === modules[i + size + 1]
      ) {
        score += N2;
      }
    }
  }
  // N4: how far the share of dark modules is from half, in whole steps of 5%.
  let dark = 0;
  for (const value of modules) dark += value;
  const total = size * size;
  return score + Math.floor(Math.abs(dark * 20 - total * 10) / total) * N4;
}

/**
 * The QR code for `text` (UTF-8 bytes, byte mode) at error correction `level`,
 * in the smallest version (1 to 40) that holds it, with the one of the 8 masks
 * that scores lowest (the first one on a tie). Throws an Error if the text
 * does not fit even in version 40.
 *
 * Cost, for a symbol n modules wide (n = 4 × version + 17): time O(8·n²), as
 * each of the 8 masks is applied, scored and undone in a few passes over the
 * grid. Memory O(n²) bytes: one Uint8Array for the modules and one for the
 * function-pattern flags. The loops over modules only read and write those two
 * arrays and allocate nothing.
 */
export function encodeQr(text: string, level: QrLevel = "M"): QrMatrix {
  const bytes = new TextEncoder().encode(text);
  const version = smallestVersion(bytes.length, level);
  const blocks = blocksFor(version, level);
  const codewords = interleave(dataCodewords(bytes, version, blocks.data), blocks);

  const size = version * 4 + 17;
  const grid: Grid = {
    size,
    modules: new Uint8Array(size * size),
    reserved: new Uint8Array(size * size),
  };
  drawFunctionPatterns(grid, version, level);
  placeCodewords(grid, codewords);

  let best = 0;
  let bestScore = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    applyMask(grid, mask);
    drawFormat(grid, level, mask);
    const score = penalty(grid);
    if (score < bestScore) {
      best = mask;
      bestScore = score;
    }
    applyMask(grid, mask);
  }
  applyMask(grid, best);
  drawFormat(grid, level, best);
  return { size, modules: grid.modules };
}
