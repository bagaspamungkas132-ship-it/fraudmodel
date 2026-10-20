# Rubrik penilaian nama (hampton-name-judge) - versi nj-1.0

Dipakai untuk menilai pasangan nama zona abu-abu secara BUTA: hanya dua nama, tanpa DOB, ID, atau data lain.
Wajib dibaca bersama `references/name-patterns.md` (normalisasi, kamus, gerbang confidence). Bila bertentangan, rubrik ini menang.
Contoh di bawah SINTETIS (bukan data nasabah). Jangan menaruh nama nasabah asli di file ini.

## Keluaran per pasangan (CSV, satu baris per pair_key)
`pair_key,verdict,case_type,confidence,reason,prompt_version`
- verdict: `SAME` | `DIFFERENT` | `UNSURE` (huruf besar persis)
- case_type: salah satu: `terpotong`, `singkatan`, `inisial_beberapa_kata`, `spasi`, `ejaan_lama_baru`, `ejaan_tionghoa`, `ejaan_arab_islami`,
  `typo_1_huruf`, `kata_tengah_hilang`, `urutan_terbalik`, `gelar`, `nama_daerah`, `ganti_nama`, `akhiran_gender`, `generasi`, `nama_berbeda`, `lainnya`
- confidence: 0.0-1.0 (jujur; SAME di bawah 0.80 dan DIFFERENT di bawah 0.70 wajib jadi UNSURE)
- reason: satu kalimat, maksimal 140 karakter, tanpa mengutip ulang nama lengkap
- prompt_version: `nj-1.0`

## Langkah keputusan (kerjakan berurutan)
1. Normalisasi (name-patterns bagian 0). Placeholder -> `UNSURE`.
2. Identik setelah normalisasi -> `SAME` 0.95 (`spasi` atau `gelar`).
3. Petakan tiap kata inti A <-> B (name-patterns bagian 12). Tidak ada kata yang berpasangan -> `DIFFERENT`.
4. Sebut SATU nama pola yang menjelaskan SEMUA selisih. Tidak bisa -> bukan `SAME`.
5. Cek penghalang: pola ganda? kata generik saja? nama pendek? dua nama valid? dugaan ganti nama? -> `UNSURE`.
6. Tentukan confidence dari tabel name-patterns bagian 14. `SAME` < 0.80 -> `UNSURE`.

## Prinsip
1. Nilai hanya pola penulisan nama. Jangan menebak dari etnis, agama, atau asumsi tentang orangnya.
2. `SAME` hanya bila setiap perbedaan dapat dijelaskan oleh SATU pola yang wajar DAN tidak ada kata inti yang berbeda tanpa penjelasan.
3. Kata inti berbeda 1-2 huruf pada kata pendek (<= 6 huruf) padahal semua kata lain identik: bisa typo, bisa orang lain (saudara, nama mirip) -> `UNSURE` kecuali ada pola ejaan yang jelas.
4. Perbedaan tegas -> `DIFFERENT`: akhiran gender berbeda (-an vs -ati, -o vs -i, putra vs putri), generasi (Jr/Sr/II/III), kata nama yang berbeda total, inisial bertentangan.
5. Dugaan ganti nama (menikah, alias, nama panggilan) -> `UNSURE`, case_type `ganti_nama`.
6. Singkatan wajar: inisial, kependekan kata ("NGR" untuk Ngurah), inisial beberapa kata ("HM" untuk dua kata), nama terpotong di akhir field.
7. Semua kata lain harus cocok. Satu kata cocok parsial tidak cukup bila kata lain berbeda.
8. Bila ragu, pilih `UNSURE`. UNSURE aman: status tidak naik, manusia memutuskan.
9. Kecocokan substring di tengah/akhir kata tidak dihitung sebagai singkatan atau terpotong.
10. Kata generik yang cocok bukan bukti (name-patterns bagian 1).
11. **Beda dari MMN:** nama nasabah tercatat lengkap di sistem, jadi sisi yang lebih pendek TIDAK otomatis terpotong.
    - Potongan huruf di kata terakhir (`UT` vs `UTAMA`) dan kata terakhir sisi pendek lolos prefix utuh -> boleh SAME.
    - Kata tambahan UTUH di belakang (`BUDI SANTOSO` vs `BUDI SANTOSO WIJAYA`) -> SAME (case_type `kata_tengah_hilang`, confidence 0.80-0.85) bila sisi pendek punya >= 2 kata penuh identik dan >= 1 di antaranya kata inti non-generik. Nama belakang/marga yang tidak tercatat adalah pola yang umum.
    - Turun ke `UNSURE` bila: yang cocok hanya kata generik (`SITI NUR` vs `SITI NUR HIDAYAH`), atau kata tambahan terlihat sebagai nama lain yang menggantikan (kedua sisi punya kata belakang berbeda = `ganti_nama`).
    - Sisi pendek hanya satu kata vs nama panjang -> `UNSURE`.
12. SAME harus bisa dibuktikan: sebut satu pola bernama dan pemetaan tiap kata inti. Tidak bisa -> UNSURE.
13. `rule_score` hanya petunjuk bobot; tidak boleh menaikkan SAME.

## Contoh sintetis (pola -> verdict)
| Nama A | Nama B | verdict | case_type |
|---|---|---|---|
| BUDI TRESNA UT | BUDI TRESNA UTAMA | SAME | terpotong |
| BUDI TRE | BUDI TRESNA UTAMA | SAME | terpotong |
| SITI WIDYA CH K | SITI WIDYA CHRISTIANA KESUMA | SAME | singkatan |
| DEWI ANGELA HM | DEWI ANGELA HENRIKA MAUREN | SAME | inisial_beberapa_kata |
| IDA BAGUS NGR PRAMANA | IDA BAGUS NGURAH PRAMANA | SAME | singkatan |
| TONO PUJI ASTUTI | TONO PUJIASTUSI | UNSURE | spasi + typo (pola ganda) |
| TONO PUJI ASTUTI | TONO PUJIASTUTI | SAME | spasi |
| ANDI NATALIJA KUSUMA PUTERI | ANDI NATALIA KUSUMAPUTRI | SAME | ejaan_lama_baru |
| SOETJIPTO WIDJAJA | SUCIPTO WIJAYA | SAME | ejaan_lama_baru |
| ACHMAD RACHMAT HIDAYAT | AHMAD RAHMAT HIDAYAT | SAME | ejaan_arab_islami |
| ABDUL RAHMAN HAKIM | ABDURRAHMAN HAKIM | SAME | ejaan_arab_islami |
| MUHAMMAD NURHADI | M NURHADI | SAME | singkatan |
| I WAYAN SUDIRA | WAYAN SUDIRA | SAME | nama_daerah |
| ANDI BUDI SANTOSO | ANDI SANTOSO | SAME | kata_tengah_hilang |
| DEWI SARI PUSPITA | PUSPITA DEWI SARI | SAME | urutan_terbalik |
| H AHMAD SYAFEI | AHMAD SYAFEI | SAME | gelar |
| CHEN LING CANDRA | CHEN LIE CANDRA | UNSURE | ejaan_tionghoa |
| RIZKI IMAM P SUTAN | RIZKI IMAN P SUTAN | UNSURE | typo_1_huruf |
| DEVI PERMATA SARI NASUTION | DESI PERMATA SARI NASUTION | UNSURE | typo_1_huruf |
| SITI AMINAH WULANDARI | SITI AMINAH SANTOSO | UNSURE | ganti_nama |
| BUDI SANTOSO | BUDI SANTOSO WIJAYA | SAME | kata_tengah_hilang (nama belakang tidak tercatat) |
| SRI WAHYUNI | SRI WAHYUNI LESTARI | SAME | kata_tengah_hilang (nama belakang tidak tercatat) |
| SITI NUR | SITI NUR HIDAYAH | UNSURE | yang cocok hanya kata generik |
| BUDI | BUDI SANTOSO | UNSURE | satu kata vs nama panjang |
| I WAYAN SUDIRA | I MADE SUDIRA | UNSURE | nama_daerah (kemungkinan saudara) |
| BUDI TRESNA UT | BUDI TRISNA UTAMA | UNSURE | truncation + typo (pola ganda) |
| DEDI KURNIAWAN | DEDI KURNIAWATI | DIFFERENT | akhiran_gender |
| BUDI SANTOSO JR | BUDI SANTOSO SR | DIFFERENT | generasi |
| SITI A RAHMAT | SITI RAHAYU RAHMAT | DIFFERENT | inisial bertentangan |
| AHMAD FAUZI | BUDI WIJAYA | DIFFERENT | nama_berbeda |

Catatan: contoh "CHEN LING / LIE" UNSURE karena kata inti berbeda; bila organisasi memutuskan kamus ejaan Tionghoa-Indonesia, pasangan yang ada di kamus itu naik ke SAME oleh rule (tahap 1), bukan oleh judge.
