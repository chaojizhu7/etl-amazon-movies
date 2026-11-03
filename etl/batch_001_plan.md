# 第一批采集计划 batch_001

- 生成时间：2026-11-03T06:14:49Z
- 规则版本：crawl_priority v0.1（review_count 降序 + asin 升序破并列）
- 范围：priority 1–100（Top-Review 前 100）
- 分层分布：high 100 / medium 0 / low 0
- 请求间隔：30s ± 10s 抖动（数据源策略 1.1 波内参数）
- **预计耗时：约 52.8 分钟**（99 间隔×30s + 100 次抓取×~2s，不含重试）
- ASIN 清单文件：`data\raw\asin_batch_001.txt`（spider -a asin_file=... 直接可读）

## 队列统计（全量）

- 总数：253,059（目标 253,059 → ✅ 一致）
- 分层：high(>100) 20,985 / medium(10-100) 83,345 / low(<10) 148,729
- 评论数范围：min 1 / max 957

## 批次清单（按 priority 升序）

| priority | asin | review_count | tier |
|----------|------|--------------|------|
| 1 | B002QZ1RS6 | 957 | high |
| 2 | B007FQDPL8 | 956 | high |
| 3 | B00005JPS8 | 925 | high |
| 4 | B001C08RHA | 925 | high |
| 5 | B001FD5KJM | 925 | high |
| 6 | B001GAPC1K | 925 | high |
| 7 | B001I1NGHY | 925 | high |
| 8 | B001KZG99A | 925 | high |
| 9 | B001FZ9AAU | 924 | high |
| 10 | B001HUHBE0 | 924 | high |
| 11 | B004JMSIRC | 924 | high |
| 12 | B000H7I6CU | 904 | high |
| 13 | 7883685635 | 902 | high |
| 14 | B00005JNS0 | 902 | high |
| 15 | B000ENV5WK | 902 | high |
| 16 | B000H5U832 | 902 | high |
| 17 | B000V1Y43W | 902 | high |
| 18 | B002LW9ITY | 902 | high |
| 19 | B0049J3QF4 | 902 | high |
| 20 | B004I654WQ | 902 | high |
| 21 | B004OBQDJ8 | 902 | high |
| 22 | B005ZMUQCK | 902 | high |
| 23 | B000S6LS66 | 886 | high |
| 24 | B000W494SI | 886 | high |
| 25 | B000W4B0Z8 | 886 | high |
| 26 | B001DWNUE2 | 886 | high |
| 27 | B008UBYTRG | 886 | high |
| 28 | B0027BOL4G | 876 | high |
| 29 | B002LBKDYE | 876 | high |
| 30 | B002UZ5CHO | 876 | high |
| 31 | B002VRZEYM | 876 | high |
| 32 | B001HN68ZU | 866 | high |
| 33 | B001KEHAFI | 866 | high |
| 34 | B001OHS8BQ | 866 | high |
| 35 | B0009XRZ9M | 865 | high |
| 36 | B000A468Y8 | 865 | high |
| 37 | B000AGTPUA | 865 | high |
| 38 | B000AGTPUK | 865 | high |
| 39 | B000AGTPUU | 865 | high |
| 40 | B000AM6MKU | 865 | high |
| 41 | B000I2JKCO | 865 | high |
| 42 | B000Q7ZO8U | 865 | high |
| 43 | B0011TNVLY | 865 | high |
| 44 | B001F12J3Y | 865 | high |
| 45 | B001F12J48 | 865 | high |
| 46 | B002NZTZ8S | 865 | high |
| 47 | B00447G29S | 865 | high |
| 48 | B000E5LEXS | 860 | high |
| 49 | B000NPNDFQ | 860 | high |
| 50 | B000S5KGH4 | 860 | high |
| 51 | B001684L0A | 857 | high |
| 52 | B001KEGR8Y | 850 | high |
| 53 | B000M341QE | 847 | high |
| 54 | B000M341QO | 847 | high |
| 55 | B000M5AJQ8 | 847 | high |
| 56 | B000M5AJQI | 847 | high |
| 57 | B000M5AJQS | 847 | high |
| 58 | B000NGTJTY | 847 | high |
| 59 | B000NHPCS0 | 847 | high |
| 60 | B000V7O0GM | 847 | high |
| 61 | B0010DRYS2 | 847 | high |
| 62 | B0010DRYSC | 847 | high |
| 63 | B002VWOMLI | 847 | high |
| 64 | B004L9GLKE | 847 | high |
| 65 | B005GHP32W | 847 | high |
| 66 | B0083SI9AY | 847 | high |
| 67 | B0090XJJT6 | 847 | high |
| 68 | B001UV4XI8 | 832 | high |
| 69 | B004MPEFBE | 832 | high |
| 70 | B004OBQDH0 | 832 | high |
| 71 | B004WMKSH2 | 832 | high |
| 72 | B004WO6BPS | 832 | high |
| 73 | B004WO774M | 832 | high |
| 74 | B004WQ1LLA | 832 | high |
| 75 | B004WX8HFG | 832 | high |
| 76 | B0064ORVCQ | 832 | high |
| 77 | B006AM4UUS | 832 | high |
| 78 | B006FXJVHY | 832 | high |
| 79 | B008UZ7MUI | 832 | high |
| 80 | B001BYLFFS | 830 | high |
| 81 | B001D2WUAC | 830 | high |
| 82 | B0032CNOK6 | 830 | high |
| 83 | B00005JNTX | 829 | high |
| 84 | B0009PBNFW | 829 | high |
| 85 | B0009VCSXW | 829 | high |
| 86 | B000A1C68S | 829 | high |
| 87 | B000ARIS52 | 829 | high |
| 88 | B000BCKFWK | 829 | high |
| 89 | B000BU0B6W | 829 | high |
| 90 | B000ML0YTE | 829 | high |
| 91 | B000ML11RI | 829 | high |
| 92 | B000ML18KS | 829 | high |
| 93 | B000ML4VKC | 829 | high |
| 94 | B001R4KQDE | 829 | high |
| 95 | B004AH59TG | 829 | high |
| 96 | B004SIP9Z6 | 829 | high |
| 97 | B004SIPA0A | 829 | high |
| 98 | B004SIPA4G | 829 | high |
| 99 | B0065AVS0K | 829 | high |
| 100 | B0065AVWGU | 829 | high |

## 执行命令（确认后运行）

```powershell
cd C:\Users\28746\Desktop\etl-amazon\src\crawler
..\..\.venv\Scripts\python.exe -m scrapy crawl product \
  -a asin_file=../../data/raw/asin_batch_001.txt \
  -s DOWNLOAD_DELAY=30 -s CONCURRENT_REQUESTS_PER_DOMAIN=1 \
  -O ../../snapshots/crawl_manifest_batch001.jsonl \
  --logfile ../../logs/crawl_batch001.log
```
