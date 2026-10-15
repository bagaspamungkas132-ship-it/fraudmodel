# AGENTS.md - Project Hampton (HSBC -> OCBC)

Baca ini di awal setiap sesi. Jawab dalam Bahasa Indonesia (istilah teknis boleh Inggris). Pemilik project: Bagas (Monitoring Insight, OCBC Indonesia).

## Konteks project
- PT Bank HSBC Indonesia mengalihkan bisnis ritel dan wealth (International Wealth and Premier Banking / IWPB) ke PT Bank OCBC NISP Tbk (OCBC Indonesia). Diumumkan awal Mei 2026.
- Skala menurut pemberitaan: sekitar 336.000 nasabah, AUM Rp89,8 triliun (investasi Rp58,2T + simpanan Rp31,6T), small retail loans Rp3,6T.
- Penyelesaian ditargetkan Q2 / semester I 2027 dan tunduk pada persetujuan regulator. Sampai selesai, layanan HSBC berjalan normal.
- "Hampton" adalah nama internal project; angka di atas dari berita publik, bukan data internal. Kalau ada konflik dengan dokumen internal, dokumen internal yang benar.

## Tugas saat ini
OCBC akan mengirim **welcome pack** ke nasabah HSBC yang akan diakuisisi. Sebelumnya wajib **dedup**: apakah nasabah HSBC ini sebenarnya sudah nasabah OCBC?

## Aturan matching (jangan diubah tanpa persetujuan Bagas)
- Primary: **Nama, DOB, ID Number**. Ketiganya cocok = MATCH.
- Nama tidak harus persis: beda gelar, disingkat, typo, urutan terbalik, ejaan lama, nama terpotong, dan kasus relevan lain.
- Secondary: **SID, NPWP**. Tertiary: **nama ibu kandung, no HP, email**.
- Secondary/tertiary hanya penguat. Tidak boleh menaikkan status ke MATCH sendirian.
- Status: MATCH / PROBABLE / REVIEW / NO_MATCH. Detail di skill `hampton-dedup`.
- Kasus ragu (kembar, ID beda 1 digit, nama berubah) masuk REVIEW untuk manusia. Jangan auto-putuskan.
- Keputusan kirim/tahan welcome pack adalah keputusan bisnis. Agent hanya memberi `suggested_action`.

## Tugas tambahan: validasi kontak dan alamat
- Validasi **no HP, email, alamat rumah** (valid format dan lengkap atau tidak).
- Alamat free text dipecah ke: jalan, nomor, RT, RW, kelurahan/desa, kecamatan, kota/kabupaten, provinsi, kode pos, dll., plus **catatan bagian mana yang kurang** (`addr_missing`, `addr_notes`, `addr_deliverable` YES/CHECK/NO).
- Validasi di sini = format dan kelengkapan. Jangan mengklaim nomor aktif atau alamat benar-benar ada. Verifikasi eksternal (OTP/HLR, geocoding, API vendor) hanya dengan persetujuan Bagas karena melibatkan PII.
- Untuk validasi nama wilayah dan kode pos butuh tabel referensi wilayah resmi; minta Bagas menyediakan dan jangan mengarang data wilayah.

## Skill yang dipakai
Dua skill terpisah (dua pekerjaan berbeda, biasanya dijalankan terpisah):
- `hampton-dedup`: matching/dedup nasabah dan cek kemiripan nama.
- `hampton-validate-contact`: validasi HP, email, alamat dan pemecahan alamat.
Pakai hanya skill yang sesuai tugas. Sub-agent: `hampton-maker`, `hampton-checker` (lihat alur maker-checker). Perbaiki script yang ada, jangan menulis ulang logika dari nol di tiap sesi.

## Alur maker-checker (dedup)
Hasil dedup tidak boleh dipakai langsung. Urutannya:
1. `subagent({ agent: "hampton-maker", task: "Jalankan dedup: HSBC=<path>, OCBC=<path>, output ke <folder_run>" })` -> `result.csv` + `maker_handoff.md`.
2. `subagent({ agent: "hampton-checker", task: "Periksa <folder_run>/result.csv. HSBC=<path>, mapping=<path>" })`. Pakai sesi baru, jangan fork, supaya independen.
3. Auditor = script, bukan agent: `python scripts/audit_run.py ... --check-report <folder_run>/checks/check_report.json --out-dir <folder_run>/audit`.
4. Verdict checker FAIL -> kembali ke maker dengan daftar defect (maksimal 2 putaran, lalu eskalasi ke Bagas).
5. PASS atau PASS_WITH_NOTES -> serahkan ke Bagas bersama ringkasan dan audit. **Persetujuan manusia wajib sebelum welcome pack dikirim.** Main agent tidak boleh menyatakan hasil "final" atau "siap kirim" sendiri.
Pekerjaan validasi HP/email/alamat (`hampton-validate-contact`) tidak memakai maker-checker kecuali Bagas memintanya.

## Aturan kerja
1. **PII dulu.** Data nasabah rahasia bank. Proses lokal; jangan kirim ke layanan eksternal; jangan commit ke git; jangan tampilkan NIK/NPWP/SID/HP/email mentah di chat atau log (mask dulu). Jangan menempelkan baris data asli ke prompt; kerjakan via script, tampilkan agregat atau sampel ter-mask.
2. **Jangan menimpa file sumber.** Output ke folder baru bertanggal, mis. `output/2026-10-08_run1/`.
3. **Lihat data sebelum menulis kode**: profil kolom, format DOB, panjang ID, null rate. Jangan mengasumsikan skema.
4. **Verifikasi, jangan klaim.** Setelah mengubah logika, jalankan `test_namematch.py` dan satu run contoh; laporkan hasil apa adanya, termasuk yang gagal.
5. **Bisa ditelusuri.** Setiap baris hasil punya reason code; tiap run mencatat `rule_version`, input, parameter, tanggal.
6. **Tanya kalau ambigu** (kolom ganda, format tanggal tak jelas, definisi "sudah nasabah"): satu pertanyaan singkat, bukan menebak.
7. **Tiap perubahan aturan** (threshold, kamus gelar/varian nama) dicatat dengan alasan dan ditambahi test.
8. Ringkas dan lugas. Laporan: angka per status dulu, lalu temuan kualitas data, lalu hal yang perlu keputusan Bagas.

## Pertanyaan terbuka (tanyakan ke Bagas bila relevan)
- Format dan lokasi file HSBC dan OCBC, serta ukurannya? Ada data WNA (paspor)?
- Apakah "sudah nasabah OCBC" mencakup rekening tutup/dormant?
- Mana yang lebih mahal: false MATCH atau false NO_MATCH? (menentukan threshold)
- Siapa reviewer untuk kasus REVIEW, dan format worksheet yang diinginkan?
- Ada exclusion list lain (meninggal, minor, do-not-contact)?
- Apakah ada tabel referensi wilayah (provinsi-kota-kecamatan-kelurahan-kode pos) yang boleh dipakai? Alamat HSBC berupa satu kolom atau beberapa kolom?
- Alamat mana yang dipakai untuk kirim: alamat KTP, domisili, atau surat-menyurat?

## Sumber berita (untuk konteks)
ocbc.id siaran pers 4 Mei 2026; Bisnis.com 5 Mei 2026; Kontan; Katadata; IDX Channel.
