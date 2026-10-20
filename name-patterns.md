# Pola & kamus bersama — versi patterns-1.0

Dipakai `hampton-name-judge` untuk KEDUA field (nama nasabah dan MMN), dibaca bersama rubrik field.
Bila bertentangan dengan rubrik field, **rubrik field menang**.
Semua contoh SINTETIS. Daftar kata di bawah adalah CONTOH pola, bukan daftar lengkap; yang tidak terdaftar dan tidak jelas polanya -> `UNSURE`.

## 0. Normalisasi (lakukan dulu, di kepala)
1. Huruf besar; titik, koma, tanda kutip, tanda hubung -> spasi; rapikan spasi ganda.
2. Buang gelar/penanda di PINGGIR nama (bagian 2) dan kata sambung (bagian 3).
3. Cek placeholder (bagian 9). Bila ya -> `UNSURE`.
4. Bila kedua string identik setelah normalisasi -> `SAME` (confidence 0.95; case_type `spasi` bila hanya tanda baca/spasi, `gelar` bila hanya gelar).
5. Sisanya: petakan kata (bagian 12) dan cari SATU pola penjelas.

## 1. Kata inti vs kata generik
- **Kata generik** = kata berfrekuensi tinggi yang tidak membedakan orang. Contoh: MUHAMMAD/MOHAMMAD/AHMAD/ABDUL, SITI, SRI, NUR, NURUL, DEWI, PUTRI/PUTRA, ADI, AGUS, BUDI, EKA, DWI, TRI, INDAH, RATNA, SARI, WATI, penanda Bali (WAYAN, MADE, NYOMAN, KETUT, PUTU, KOMANG, GEDE).
- **Kata inti** = kata selain generik (nama pembeda, marga, nama belakang).
- Kata generik yang cocok **bukan bukti**. SAME butuh salah satu:
  a. minimal SATU kata inti yang cocok penuh (>= 4 huruf), atau
  b. minimal DUA kata (generik boleh) yang cocok penuh pada posisi yang sama.
- Bila yang cocok hanya kata generik (mis. `SRI` vs `SRI MULYANI`, `SITI` vs `SITI AMINAH`, `NUR` vs `NUR HIDAYAH`) -> maksimal `UNSURE`, confidence <= 0.75.

## 2. Gelar & penanda (abaikan HANYA bila di pinggir)
- Agama/kehormatan: HJ, HJJ, H, HAJI, HAJJAH, KH, KYAI, UST, USTADZ, DR, DRS, DRA, IR, PROF, DRG, NY, NYONYA, BU, IBU, BPK, BAPAK, MR, MRS, ME, ALM, ALMARHUM, ALMARHUMAH.
- Akademik di belakang: SH, SE, SKOM, SPD, ST, SSI, SKM, SIP, AMD, AMK, MM, MSI, MH, MKOM, MBA, PHD.
- Penanda bangsawan/daerah (dropnya = `nama_daerah`): RADEN, RD, RM, RA, TB, TUBAGUS, RATU, DAENG, DG, I, NI, IDA BAGUS, IB, ANAK AGUNG, AA, GUSTI, GST, COKORDA, DEWA.
- Kata di TENGAH nama (mis. `SE`, `ST`, `MM`, `H`) bukan gelar, mungkin inisial -> jangan dibuang.
- `H` atau `M` tunggal di awal bisa Haji/Muhammad atau inisial. Bila membuang atau menganggapnya membuat kedua sisi cocok -> SAME `gelar`, confidence maks 0.85.
- `ANDI` (Bugis) adalah bagian nama, bukan gelar buangan.

## 3. Kata sambung (abaikan)
BIN, BINTI, BT, BINTE, AL, EL, VAN, DE, DER, DEN. Kata setelah BIN/BINTI biasanya nama ayah: perlakukan sebagai kata tambahan di belakang, bukan bukti beda.

## 4. Spasi / penyatuan kata
- `PUJI ASTUTI` = `PUJIASTUTI`; `HARI SANTOSO` = `HARISANTOSO`. Setelah semua spasi dibuang string identik -> SAME `spasi` (0.90).
- Kata majemuk Arab: `ABDUL RAHMAN` / `ABDURRAHMAN` / `ABDURAHMAN` / `ABDUR RACHMAN` -> SAME (`ejaan_arab_islami` di nj, `spasi` di mmn), confidence 0.80-0.85. Ini dihitung SATU pola (penulisan kata majemuk).

## 5. Singkatan & inisial
- Kamus singkatan wajar: MUHAMMAD = M/MUH/MHD/MOH/MOCH/MOHD; ABDUL = ABD; NGURAH = NGR; WAYAN = WYN; KETUT = KTUT; KOMANG = KMG; GEDE = GD; MADE = MD; SRI = SR.
- Inisial cocok bila huruf pertama kata di sisi panjang sama (`SITI R` vs `SITI RAHAYU`).
- Inisial gabungan: `HM` = huruf awal dua kata berurutan (`HENRIKA MAUREN`).
- SAME hanya bila ada minimal SATU kata inti cocok penuh DAN inisial cocok. Inisial saja tanpa kata penuh -> `UNSURE`.
- **Inisial bertentangan** (`SITI A` vs `SITI RAHAYU`) -> `DIFFERENT`.

## 5b. Truncation (potongan di akhir field)
- Potongan hanya di kata TERAKHIR sisi pendek. Kata-kata sebelumnya identik penuh.
- Kata terakhir sisi pendek identik, atau prefix utuh (>= 2 huruf) dari kata di posisi yang sama di sisi panjang.
- Kata tambahan di sisi panjang hanya di BELAKANG.
- Potongan 2 huruf tidak bisa membedakan kandidat (`UT` bisa `UTAMA`/`UTOMO`). Bila sisi panjang punya kata lengkap yang jelas berbeda dari potongan, bukan truncation.
- BUKAN truncation: substring di tengah/akhir kata, kata yang hanya sama 2-3 huruf awal, urutan posisi berbeda.

## 6. Ejaan
**Lama <-> baru (konsisten, satu keluarga pola):** OE=U, DJ=J, TJ=C, J=Y (`TJAHJONO`=`CAHYONO`), SJ=SY, NJ=NY, CH=KH/H, `-IJ`=`-IY`, PUTERI=PUTRI.
Contoh: `SOETJIPTO` = `SUCIPTO`; `WIDJAJA` = `WIJAYA`; `SOEHARTO` = `SUHARTO`. Beberapa konversi dalam satu pasangan dihitung SATU pola `ejaan_lama_baru` selama semuanya masuk daftar.

**Arab/Islami (varian baku-tidak baku):** MUHAMMAD/MOHAMMAD/MUHAMAD/MOHAMAD; ACHMAD/AHMAD/AKHMAD; RACHMAT/RAHMAT; RACHMAN/RAHMAN; KHAIRUL/KHOIRUL/KHAERUL; ZAINAL/ZAENAL; SOLEH/SHOLEH/SALEH; YUSUF/YUSUP/JUSUF; SYARIF/SARIF; HASAN/HASSAN; ABDULLAH/ABDULAH; AISYAH/AISYAH/AISAH. Di luar daftar dan tidak jelas -> `UNSURE`.

**Tionghoa (sistem Hokkien/Hanzi yang sama):** TJ/C/CH, OE/U, LIE/LEE/LI, LIEM/LIM, OEY/OEI, KWEI/KWAI, MIE/MEI, NIO/NIU. Marga beda sistem (TAN vs CHEN, OEI vs HUANG) tanpa kamus organisasi -> `UNSURE`.

**Penting:** ejaan + pola lain (truncation, typo, urutan) = pola ganda -> `UNSURE`.

## 7. Typo
SAME `typo_1_huruf` hanya bila SEMUA terpenuhi:
1. Kata >= 7 huruf, selisih 1 huruf (substitusi, sisip, hapus, atau dua huruf bertukar tempat); selisih 2 huruf hanya bila kata >= 9 huruf.
2. Hasil selisih BUKAN nama valid lain yang umum (`SANTOSO`/`SANTOSA`, `DEVI`/`DESI`, `IMAM`/`IMAN`, `SUMIATI`/`SUGIARTI` = nama valid semua).
3. Itu SATU-SATUNYA selisih dalam pasangan.
Kata <= 6 huruf beda 1 huruf -> `UNSURE`; beda >= 2 huruf -> `DIFFERENT` kecuali ada pola ejaan.
Huruf ganda (`AHMADD`, `HASAN`/`HASSAN`) dianggap typo bila bukan varian kamus.

## 8. Urutan terbalik
Semua kata identik, hanya urutan beda (`SARI DEWI` vs `DEWI SARI`) -> SAME `urutan_terbalik`, confidence 0.82-0.88. Bila ada kata yang tidak identik -> pola ganda -> `UNSURE`.

## 9. Placeholder & noise -> `UNSURE` (case_type `lainnya`, confidence <= 0.6)
`-`, `.`, `NA`, `N/A`, `NN`, `XXX`, `TIDAK ADA`, `TANPA NAMA`, `IBU KANDUNG`, `IBU`, `MAMA`, `UNKNOWN`, angka murni, string 1 huruf. Dua placeholder yang sama bukan bukti satu orang.

## 10. Ganti nama / alias / menikah
- Satu nama diakhiri/ditambah nama lain yang berbeda total (`SITI AMINAH WULANDARI` vs `SITI AMINAH SANTOSO`) -> `UNSURE` (`ganti_nama`).
- Mengandung `ALIAS`, `AKA`, `A.K.A`, `A/N`, `PANGGILAN`: pisahkan tiap sisi; bila salah satu komponen cocok dengan sisi lain, tetap maksimal `UNSURE`.
- Kata tengah hilang (`ANDI BUDI SANTOSO` vs `ANDI SANTOSO`): SAME `kata_tengah_hilang` hanya bila kata pertama DAN terakhir identik penuh, hasil >= 2 kata, dan confidence 0.80-0.85.

## 11. Pembeda tegas -> `DIFFERENT`
- Akhiran gender: -O/-I (`SUGIANTO`/`SUGIANTI`), -AN/-ATI, -US/-A (`AGUSTINUS`/`AGUSTINA`), PUTRA/PUTRI.
- Generasi: JR/SR, II/III/IV.
- Inisial bertentangan.
- Semua kata inti berbeda tanpa pola penjelas.

Urutan lahir Bali yang beda (`I WAYAN X` vs `I MADE X`) = kemungkinan saudara -> `UNSURE` (`nama_daerah`), bukan SAME.

## 12. Pemetaan kata (wajib sebelum SAME)
Petakan tiap kata inti A ke kata di B (identik, prefix, inisial, atau varian kamus). Kata tanpa pasangan di kedua sisi = kata inti berbeda -> `DIFFERENT`. Kata hanya cocok sebagian/substring -> maksimal `UNSURE`.

## 13. Pola ganda
SAME butuh SATU nama pola (satu keluarga). Dua keluarga pola ditumpuk (typo + kata tambahan, ejaan + truncation, urutan + typo) -> `UNSURE`.

## 14. Gerbang confidence
| Situasi | Confidence wajar |
|---|---|
| Identik setelah normalisasi / hanya gelar | 0.90-0.95 |
| Truncation, >= 2 kata penuh identik | 0.85-0.92 |
| Semua selisih terdaftar di kamus ejaan | 0.85-0.90 |
| Spasi / penyatuan kata | 0.90 |
| Typo 1 huruf kata >= 7, bukan nama valid | 0.80-0.85 |
| Inisial + >= 1 kata inti penuh | 0.80-0.88 |
| Urutan terbalik, semua kata identik | 0.82-0.88 |
| Kata tengah hilang | 0.80-0.85 |
| Hanya kata generik yang cocok | maks 0.75 -> UNSURE |

Aturan:
- `SAME` dengan confidence < 0.80 DILARANG -> tulis `UNSURE`.
- `DIFFERENT` dengan confidence < 0.70 -> tulis `UNSURE`.
- `rule_score` tidak pernah menaikkan confidence.
