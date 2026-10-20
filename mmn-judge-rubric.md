# Rubrik penilaian nama ibu kandung (MMN) — versi mmn-1.0

Dipakai `hampton-name-judge` untuk menilai pasangan nama ibu kandung (MMN) secara **BUTA**: hanya dua nama (`mmn_a`, `mmn_b`), tanpa DOB, ID, nama nasabah, atau data lain.
Wajib dibaca bersama `references/name-patterns.md` (normalisasi, kamus, gerbang confidence). Bila bertentangan, rubrik ini menang.
Contoh di bawah SINTETIS. Jangan menaruh data nasabah asli di file ini.

## Keluaran per pasangan (CSV, satu baris per pair_key)
`pair_key,verdict,case_type,confidence,reason,prompt_version`
- verdict: `SAME` | `DIFFERENT` | `UNSURE` (huruf besar persis)
- case_type: salah satu: `terpotong`, `singkatan`, `inisial`, `spasi`, `ejaan_lama_baru`, `ejaan_tionghoa`, `typo_1_huruf`, `gelar_me_ny_bu`, `gelar_siti_nik_almarhumah`, `kata_tengah_hilang`, `urutan_terbalik`, `nama_berbeda`, `lainnya`
- confidence: 0.0–1.0 (jujur). high = 0.85+, medium = 0.7–0.84, low = <0.7.
- reason: satu kalimat, maks 140 karakter, tanpa mengutip ulang nama lengkap.
- prompt_version: `mmn-1.0`
- Pemetaan pola yang tidak punya case_type sendiri: ejaan Arab/Islami -> `ejaan_lama_baru`; nama daerah, ganti nama, placeholder, kata sambung -> `lainnya`.

## Konteks khusus MMN (berbeda dari nama nasabah)
- MMN adalah **nama ibu kandung nasabah** — sering tercatat pendek/terpotong di salah satu bank (hanya nama depan, atau tanpa marga).
- Truncation satu sisi memang kasus yang sering, TAPI harus lolos definisi ketat di bawah. "Sisi lebih pendek" tidak otomatis berarti "terpotong".
- Gelar/penanda umum: `ME`, `NY`, `BU`, `SITI`, `NIK`, `ALM`, `ALMARHUMAH` — abaikan (bukan bagian nama inti), kecuali yang tersisa hanya kata generik (lihat name-patterns bagian 1).

## Langkah keputusan (kerjakan berurutan)
1. Normalisasi (name-patterns bagian 0). Placeholder (`IBU KANDUNG`, `-`, `TIDAK ADA`, dst.) -> `UNSURE`. Jika sisa kosong setelah buang penanda -> `UNSURE`.
2. Identik setelah normalisasi -> `SAME` 0.95.
3. Petakan tiap kata inti A <-> B (name-patterns bagian 12). Tidak ada satu pun kata yang berpasangan -> `DIFFERENT`.
4. Sebut SATU nama pola yang menjelaskan SEMUA selisih. Tidak bisa -> bukan `SAME`.
5. Cek penghalang: pola ganda? hanya kata generik yang cocok? nama pendek? dua nama valid? -> `UNSURE`.
6. Tentukan confidence dari tabel name-patterns bagian 14. `SAME` dengan confidence < 0.80 -> turunkan ke `UNSURE`.

## Aturan SAME
1. **Truncation satu sisi (ketat)**: sisi pendek adalah potongan awal sisi panjang.
   a. Semua kata di sisi pendek KECUALI kata terakhir identik penuh dengan kata di posisi yang sama di sisi panjang.
   b. Kata terakhir sisi pendek identik ATAU prefix utuh (>= 2 huruf) dari kata di posisi yang sama di sisi panjang (`ABI TRESNA UT` vs `ABI TRESNA UTAMA`).
   c. Kata tambahan di sisi panjang hanya di BELAKANG (marga, nama ayah, nama suami).
   d. Bila sisi pendek hanya SATU kata: kata itu harus identik penuh atau prefix >= 4 huruf, DAN bukan kata generik (`LINA` vs `LINA SAFITRI` = SAME; `SRI` vs `SRI MULYANI` = UNSURE karena SRI generik).
   BUKAN truncation: substring di tengah/akhir kata, kata yang hanya sama 2–3 huruf awal tanpa kata identik lain, urutan posisi berbeda.
2. **Inisial/singkatan**: inisial cocok huruf awal DAN ada >= 1 kata inti penuh yang identik (`SR M VERONICA` vs `SRI M VERONICA`, `SITI R` vs `SITI RAHAYU`). Inisial saja tanpa kata penuh -> UNSURE.
3. **Typo 1–2 huruf** hanya bila kata >= 7 huruf, hasil selisih bukan nama valid lain yang umum, dan itu SATU-SATUNYA pola (`SUPRIATI` vs `SUPRIYATI`). `HARTINI` vs `HARTINA` = 7 huruf tapi akhiran gender/nama valid -> UNSURE.
4. **Varian romanisasi Tionghoa** (sistem sama, semua selisih terdaftar di kamus): `MIE` vs `MEI`, `NIO` vs `NIU`, `KWEI` vs `KWAI`, `LIEM` vs `LIM` -> SAME `ejaan_tionghoa`. Semua kata lain harus identik.
5. **Ejaan lama/baru & Arab** (name-patterns bagian 6): `SOETJIPTO` vs `SUCIPTO`, `RACHMAWATI` vs `RAHMAWATI` -> SAME `ejaan_lama_baru`.
6. **Gelar SITI/NIK/ALMARHUMAH berbeda**: `SITI MASITOH` vs `MASITOH` -> SAME `gelar_siti_nik_almarhumah`, asalkan sisanya kata inti non-generik. `SITI RUSNI` vs `SITI RUSNAH` -> UNSURE (typo + nama pendek).
7. **Urutan kata terbalik**, semua kata identik -> SAME `urutan_terbalik`.
8. **Spasi/penyatuan** (`PUJI ASTUTI` vs `PUJIASTUTI`) -> SAME `spasi`.
9. **Kata tengah hilang** (kata pertama dan terakhir identik, sisa >= 2 kata) -> SAME `kata_tengah_hilang`, confidence 0.80–0.85.

## Aturan UNSURE
- Hanya kata generik yang cocok (`SRI` vs `SRI MULYANI`, `NUR` vs `NURUL HIDAYAH`).
- Pola ganda (typo + kata tambahan, ejaan + truncation, urutan + typo).
- Kata inti kedua berbeda tanpa penjelasan tapi kata pertama identik panjang (`KUSTANI` vs `PUSPANI`).
- Nama pendek (<= 6 huruf) beda 1 huruf, atau kedua varian adalah nama valid yang umum (`RUMIATI` vs `RUGIARTI`).
- Romanisasi yang sistemnya tidak jelas, atau marga beda sistem tanpa kamus.
- Placeholder (`IBU KANDUNG`, `-`, `TIDAK ADA`).
- Kecocokan hanya substring di tengah/akhir kata tapi ada sedikit petunjuk lain (turun dari DIFFERENT bila confidence < 0.70).
- Bila ragu, `UNSURE`. UNSURE aman: status tidak berubah, manusia memutuskan.

## Aturan DIFFERENT
- **Semua kata inti berbeda** tanpa pola penjelas (`RINA PANG` vs `LONA TENG`, `INTAN PURWANI SUTANTO` vs `TAN POO ING`).
- Kata hanya sama 1–2 huruf awal atau hanya cocok sebagai substring tengah/akhir, dan tidak ada kata lain yang berpasangan.
- Akhiran gender berbeda tegas (`SUGIANTO` vs `SUGIANTI`, `-AN` vs `-ATI`) pada nama inti yang sama.
- Inisial bertentangan (`SITI A` vs `SITI RAHAYU`).
- Confidence DIFFERENT < 0.70 -> tulis `UNSURE`.

## Prinsip
1. Nilai hanya pola penulisan, jangan menebak dari etnis/agama.
2. `SAME` hanya bila setiap perbedaan terjelasan SATU pola wajar DAN tidak ada kata inti berbeda tanpa penjelasan.
3. Sisi lebih pendek boleh SAME HANYA bila lolos definisi truncation ketat. Kemiripan "terasa mirip" bukan bukti.
4. SATU pola saja. Dua pola ditumpuk -> UNSURE.
5. Kata generik yang cocok bukan bukti.
6. SAME harus bisa dibuktikan: sebut satu pola bernama dan pemetaan tiap kata inti. Tidak bisa -> UNSURE.
7. Gerbang confidence: SAME >= 0.80; di bawahnya UNSURE. `rule_score` tidak boleh menaikkan SAME.

## Contoh sintetis — SAME
| MMN A | MMN B | verdict | case_type | conf |
|---|---|---|---|---|
| LINA | LINA SAFITRI | SAME | terpotong | 0.82 |
| ABI TRESNA UT | ABI TRESNA UTAMA | SAME | terpotong | 0.90 |
| ABI TRESNA UT | ABI TRESNA UTAMA WIJAYA | SAME | terpotong | 0.88 |
| ABI TRE | ABI TRESNA UTAMA | SAME | terpotong | 0.85 |
| SUPRIATI | SUPRIYATI | SAME | typo_1_huruf | 0.84 |
| SR M VERONICA | SRI M VERONICA | SAME | inisial | 0.86 |
| SITI MASITOH | MASITOH | SAME | gelar_siti_nik_almarhumah | 0.88 |
| MEI LIEM | MIE LIEM | SAME | ejaan_tionghoa | 0.85 |
| SOETJIPTO LESTARI | SUCIPTO LESTARI | SAME | ejaan_lama_baru | 0.88 |
| DEWI SARI | SARI DEWI | SAME | urutan_terbalik | 0.84 |
| PUJI ASTUTI | PUJIASTUTI | SAME | spasi | 0.90 |

## Contoh sintetis — UNSURE / DIFFERENT (jangan SAME)
| MMN A | MMN B | verdict | alasan |
|---|---|---|---|
| RUMIATI | RUGIARTI LIEM | UNSURE | typo + kata tambahan (pola ganda), keduanya nama valid |
| SRI | SRI MULYANI | UNSURE | hanya kata generik yang cocok |
| KO | KORATI | UNSURE | satu kata terpotong, prefix < 4 huruf |
| LINA | LINDA SARI | UNSURE | nama pendek beda 1 huruf, bukan prefix utuh |
| ABI TRESNA UT | ABI TRISNA UTAMA | UNSURE | truncation + typo (pola ganda) |
| SUGIANA | SUGIANTI | UNSURE | typo + akhiran gender, pola ganda |
| IBU KANDUNG | IBU KANDUNG | UNSURE | placeholder, bukan bukti satu orang |
| KUSRATI | KUO | DIFFERENT | KUO bukan prefix KUSRATI, hanya sama 1–2 huruf awal |
| RINTI SOPATI | SOA HORINTI | DIFFERENT | RINTI hanya substring di akhir kata, kata lain tak berpasangan |
| SITI A | SITI RAHAYU | DIFFERENT | inisial bertentangan |
| SUGIANTO | SUGIANTI | DIFFERENT | akhiran gender |
| RINA PANG | LONA TENG | DIFFERENT | semua kata inti berbeda |
