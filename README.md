# FLiNG Trainer + Steam Deck Compatibility Checker

Script otomasi untuk mencari game yang punya trainer (cheat) dari [FLiNG Trainer](https://flingtrainer.com/) dan mengecek kompatibilitasnya dengan Steam Deck, termasuk harga dan rating di Steam Store.

Supports multiple regions/currencies — works worldwide, not just Indonesia!

## Tujuan

Membantu memilih game yang **enjoyable di Steam Deck** — karena punya trainer dan compatible.

## Yang Dilakukan Script

1. **Scrape FLiNG Trainer** — ambil daftar game yang punya trainer (tahun configurable, default 2024+)
2. **Cari di Steam Store** — mapping nama game ke Steam App ID
3. **Cek Steam Deck compatibility** — Verified / Playable / Unsupported / Unknown
4. **Cek harga** — dalam mata uang lokal (configurable per country)
5. **Cek rating** — persentase review positif dan total review
6. **Output ke Excel** — formatted, color-coded, sorted by Deck compatibility

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run with defaults (ID/IDR, trainers from 2024+)
python fling_steam_checker.py

# Use USD pricing (United States)
python fling_steam_checker.py --country US

# Include older trainers and use JPY
python fling_steam_checker.py --year 2023 --country JP

# Fresh run, ignore cache
python fling_steam_checker.py --no-cache

# Show all options
python fling_steam_checker.py --help
```

## CLI Options

| Option | Default | Description |
|---|---|---|
| `--year` | `2024` | Minimum trainer year (older trainers are skipped) |
| `--country` | `ID` | Steam store country code for pricing |
| `--workers` | `4` | Number of concurrent threads for Steam API |
| `--delay` | `1.5` | Seconds between API requests per game |
| `--retries` | `3` | Max retry attempts for transient API errors |
| `--ttl-hours` | `24` | Skip price refresh if cached within N hours |
| `--output-dir` | current dir | Output directory for Excel and cache files |
| `--no-cache` | off | Ignore cache, fetch all data fresh |
| `--verbose` | off | Show debug-level output |

### Supported Country Codes

AR, AU, BR, CA, CN, EU, GB, ID, IN, JP, KR, MX, NO, NZ, PH, PL, RU, SA, SE, SG, TH, TR, TW, UA, US, ZA

Each country code maps to the appropriate currency symbol and formatting (e.g., `--country JP` → ¥ JPY, `--country US` → $ USD).

## Output

- **Excel file**: `fling_steam_deck_YYYYMMDD_HHMM.xlsx` in the output directory
- **Cache file**: `fling_steam_cache.json` (auto-created/updated in output directory)

### Excel berisi 2 sheet:

**Sheet "FLiNG + Steam Deck":**

| Kolom | Keterangan |
|---|---|
| Game Name | Nama game |
| Trainer Date | Tanggal trainer dirilis di FLiNG |
| Options | Jumlah opsi cheat yang tersedia |
| Last Updated | Terakhir trainer diupdate |
| Steam Name | Nama game di Steam (bisa beda sedikit) |
| Steam App ID | ID unik game di Steam |
| Deck Compatibility | Verified (hijau), Playable (kuning), Unsupported (merah), Unknown |
| Price (XXX) | Harga saat ini dalam mata uang lokal (XXX = kode mata uang) |
| Original Price | Harga sebelum diskon |
| Discount % | Persentase diskon (kalau ada, background hijau) |
| On Sale | YES kalau sedang diskon |
| Rating | Deskripsi rating (Overwhelmingly Positive, Very Positive, dll.) |
| Rating % | Persentase review positif |
| Total Reviews | Jumlah total review |
| Trainer URL | Link ke halaman trainer di FLiNG |
| Steam URL | Link ke Steam Store |

**Sheet "Summary":** Statistik ringkasan (total, breakdown Deck status, jumlah on sale, free games, region info).

### Warna di Excel:
- **Hijau** pada Deck Compatibility = Verified
- **Kuning** = Playable
- **Merah** = Unsupported
- **Hijau muda** pada kolom Discount/On Sale = sedang diskon

### Sorting:
Data di-sort: Deck Verified paling atas, lalu Playable, lalu sisanya. Di dalam tiap grup, di-sort by rating tertinggi.

## Sistem Cache

Script menggunakan cache (`fling_steam_cache.json`) supaya run berikutnya lebih cepat:

| Scenario | Estimasi Waktu | Penjelasan |
|---|---|---|
| **Run pertama** (tanpa cache) | ~5-6 menit | Full scrape 228+ games, 4 API calls per game (concurrent) |
| **Run ulang** (ada cache, 0 game baru) | ~15 detik | Skip scrape & Steam lookup, hanya refresh harga yang stale |
| **Run ulang** (semua harga fresh < 24h) | ~5 detik | Hanya scrape 1 halaman, semua price di-skip oleh TTL |
| **Run setelah ada game baru** | ~1-2 menit | Fetch game baru + refresh harga yang stale |

### Apa yang di-cache:
- Steam App ID, nama, Deck compatibility, reviews/rating
- Timestamp `last_fetched` dan `_price_updated_at` untuk TTL
- Data ini jarang berubah, jadi aman di-cache

### Apa yang selalu di-refresh:
- **Harga dan diskon** — karena sering berubah (sale events, dll.)
- Hanya yang **stale** (>24 jam sejak terakhir update) yang di-refresh; data fresh di-skip

### Reset cache:
Kalau mau full refresh dari awal, hapus file cache:

```bash
rm fling_steam_cache.json
```

Atau gunakan flag `--no-cache`:

```bash
python fling_steam_checker.py --no-cache
```

## Performance

Script menggunakan **concurrent requests** (default: 4 threads) untuk mempercepat Steam API calls. Transient errors (timeout, 5xx) otomatis di-retry 3x dengan exponential backoff (2s, 4s, 8s).

## Dependencies

```
pip install -r requirements.txt
```

Packages: `requests`, `beautifulsoup4`, `openpyxl`, `tqdm`

## Tips

- **Jalankan seminggu sekali** atau sebelum Steam sale event untuk cek diskon terbaru
- **Filter di Excel**: pakai auto-filter di header untuk filter Deck = "Verified" + On Sale = "YES" untuk deal terbaik
- **Nama game tidak cocok?** Beberapa game punya nama beda di FLiNG vs Steam — cek kolom "Steam Name" vs "Game Name"
- **Concurrent requests**: 4 thread paralel mempercepat price refresh dari ~3 menit jadi ~13 detik
- **Auto-retry**: Transient errors (timeout, 5xx) otomatis di-retry 3x dengan exponential backoff

## License

MIT License — see [LICENSE](LICENSE).