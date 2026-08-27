# Báo cáo Reliability Lab — Day 25 Track 3

**Họ tên:** Phạm Công Đăng
**MSSV:** 2A202601280
**Mã bài:** K4-Day25-Track3-Reliability-Agent
**Ngày nộp:** 27/08/2026....

---

## 1. Tổng quan kiến trúc

`ReliabilityGateway` định tuyến mỗi prompt qua 3 lớp, theo đúng thứ tự: cache ngữ nghĩa (semantic cache) → circuit breaker riêng cho từng provider → thông báo fallback tĩnh.

```
User Request
    |
    v
[Gateway.complete(prompt)]
    |
    v
[Cache check] --HIT (score >= 0.92)--> trả text đã cache, route="cache_hit:<score>"
    | MISS
    v
[Circuit Breaker: primary]  --OPEN?--> bỏ qua, thử provider tiếp theo
    | allow_request() == True
    v
provider.complete(prompt) --thành công--> cache.set(), route="primary", trả kết quả
    | ProviderError / CircuitOpenError
    v
[Circuit Breaker: backup] --OPEN?--> bỏ qua
    | allow_request() == True
    v
provider.complete(prompt) --thành công--> cache.set(), route="fallback", trả kết quả
    | tất cả provider đều fail
    v
[Static fallback] route="static_fallback", error=<lỗi provider cuối cùng>
```

State machine của circuit breaker cho mỗi provider: `CLOSED -> OPEN` sau `failure_threshold` lần fail liên tiếp; `OPEN -> HALF_OPEN` sau `reset_timeout_seconds`; `HALF_OPEN -> CLOSED` khi đạt `success_threshold` lần probe (thử) thành công liên tiếp, hoặc quay lại `OPEN` ngay lập tức nếu probe fail (reason `probe_failure`, được tách riêng khỏi `failure_threshold_reached`).

## 2. Cấu hình (Configuration)

| Tham số                | Giá trị | Lý do                                                                                                                                                                                                                                           |
| ----------------------- | --------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| failure_threshold       |         3 | Mở circuit sau 3 lần fail liên tiếp — chấp nhận vài lỗi thoáng qua (transient) mà không mở ngay sau 1 lần lỗi, nhưng vẫn phản ứng đủ nhanh trong một "cửa sổ retry".                                                     |
| reset_timeout_seconds   |         2 | Đủ ngắn để phát hiện nhanh khi provider hồi phục thật (trong môi trường mô phỏng này request chỉ mất ~200-300ms), đủ dài để không dồn dập gửi request vào provider vẫn đang down.                                  |
| success_threshold       |         1 | Chỉ cần 1 probe thành công là đóng lại circuit — chọn để hồi phục nhanh trong bài lab traffic thấp; hệ thống production traffic cao nên dùng 2-3 để tránh "flapping" (đóng/mở liên tục) do 1 probe may mắn.         |
| cache TTL (ttl_seconds) |      300s | Phù hợp với đặc tính câu hỏi FAQ/ngắn hạn trong bộ dữ liệu mẫu; đủ dài để hấp thụ traffic dồn dập, đủ ngắn để câu trả lời "có mốc thời gian" (giá, năm chính sách...) không bị cũ quá lâu.            |
| similarity_threshold    |      0.92 | Thử 0.85 trước — bị false hit với các câu chỉ khác nhau ở năm/số ID (VD: "2024" vs "2026"), bị`_looks_like_false_hit()` bắt được. Tăng lên 0.92 để yêu cầu câu hỏi gần như giống hệt mới tin tưởng cache hit. |

**Ví dụ false-hit thật (chạy trực tiếp `ResponseCache`):**

```
cache.set("Summarize refund policy for 2024 deadline", "Old refund policy")
cache.get("Summarize refund policy for 2026 deadline")
-> cached = None, similarity score = 0.9375

false_hit_log = [{
  "query": "Summarize refund policy for 2026 deadline",
  "cached_key": "Summarize refund policy for 2024 deadline",
  "score": 0.9375,
  "reason": "date_or_number_mismatch"
}]
```

Hai câu hỏi giống nhau tới 93.75% theo n-gram cosine (chỉ khác token năm) — cao hơn cả ngưỡng thô 0.85 lẫn ngưỡng 0.92 đang dùng. Nếu không có guardrail `_looks_like_false_hit()` (so sánh các số 4 chữ số), hệ thống sẽ trả nhầm chính sách của năm 2024 cho câu hỏi về năm 2026. Guardrail đã chặn đúng và ghi log lại thay vì âm thầm trả dữ liệu cũ (stale).

| load_test requests | 100 mỗi scenario (300 tổng cộng cho 3 scenario) | Đủ mẫu để ước lượng P95/P99 ổn định mà không làm lab chạy quá lâu. |

## 3. Định nghĩa SLO

| SLI                   | Mục tiêu SLO | Giá trị thực tế (cache memory) | Đạt?                                 |
| --------------------- | -------------- | ---------------------------------: | -------------------------------------- |
| Availability          | >= 99%         |                             98.67% | Không — hụt nhẹ so với mục tiêu |
| Latency P95           | < 2500 ms      |                          316.09 ms | Đạt                                  |
| Fallback success rate | >= 95%         |                             95.00% | Đạt (đúng ngưỡng)                |
| Cache hit rate        | >= 10%         |                              59.0% | Đạt                                  |
| Recovery time         | < 5000 ms      |                            2255 ms | Đạt                                  |

Availability hụt 1.33 điểm so với mục tiêu — nguyên nhân hoàn toàn đến từ scenario `primary_timeout_100`: khi primary đã bị cắt hoàn toàn, backup (fail rate 5%) là nguồn duy nhất, và chính những lần fail hiếm hoi của backup lộ ra thành `static_fallback` trước khi circuit breaker kịp hỗ trợ gì thêm (xem mục Phân tích lỗi).

## 4. Metrics

Từ `reports/metrics.json` (cấu hình: cache memory bật, `configs/default.yaml`). Bản xuất CSV được làm phẳng của cùng lần chạy (qua `RunMetrics.write_csv()`, các trạng thái scenario được mở rộng thành cột `scenario_<name>`) nằm ở `reports/metrics.csv`:

| Metric                | Giá trị |
| --------------------- | --------: |
| total_requests        |       300 |
| availability          |    0.9867 |
| error_rate            |    0.0133 |
| latency_p50_ms        |    277.08 |
| latency_p95_ms        |    316.09 |
| latency_p99_ms        |    320.20 |
| fallback_success_rate |      0.95 |
| cache_hit_rate        |      0.59 |
| circuit_open_count    |        10 |
| recovery_time_ms      |   2255.01 |
| estimated_cost        |  0.051668 |
| estimated_cost_saved  |     0.177 |

## 5. So sánh có/không có cache

Chạy cùng 3 scenario (300 request) với `configs/default.yaml` (bật cache, backend memory) và `configs/no_cache.yaml` (tắt cache):

| Metric             | Không cache | Có cache | Chênh lệch                                                                                                                          |
| ------------------ | -----------: | --------: | ------------------------------------------------------------------------------------------------------------------------------------- |
| availability       |       0.9667 |    0.9867 | +0.02                                                                                                                                 |
| latency_p50_ms     |       277.02 |    277.08 | ~0 (cache hit ghi nhận latency 0ms, nhưng P50 vẫn bị chi phối bởi các request miss cache — 41% request vẫn miss ở mức P50) |
| latency_p95_ms     |       316.56 |    316.09 | -0.47                                                                                                                                 |
| circuit_open_count |           22 |        10 | -12 (cache hấp thụ các câu hỏi lặp lại, nên ít request chạm tới primary đang lỗi hơn)                                   |
| estimated_cost     |      0.12067 |  0.051668 | -0.069 (-57%)                                                                                                                         |
| cache_hit_rate     |          0.0 |      0.59 | +0.59                                                                                                                                 |

Kết luận: giá trị lớn nhất của cache trong bài lab này không nằm ở latency thô (provider call vốn đã nhanh trong môi trường mô phỏng) — mà là giảm chi phí (-57%) và giảm áp lực lên circuit breaker (ít gọi provider hơn = ít cơ hội để breaker bị trip hơn).

## 6. Redis shared cache

- Vì sao cache in-memory không đủ cho triển khai nhiều instance: `ResponseCache` lưu entry trong một list Python thường ngay trên tiến trình gateway. Nếu gateway được scale lên N instance sau load balancer, mỗi instance tự xây cache riêng từ đầu — một câu hỏi đã được cache ở instance A vẫn là cache miss lạnh (cold miss) ở instance B, khiến hit rate thực tế (và tiền tiết kiệm được) sụt giảm khi số instance tăng.
- `SharedRedisCache` giải quyết thế nào: entry cache được ghi vào Redis (`HSET` + `EXPIRE`) dưới một namespace key dùng chung (`rl:cache:<md5(query)>`). Bất kỳ instance gateway nào trỏ tới cùng Redis URL đều thấy ngay entry đó — không cần sticky session hay filesystem dùng chung.

### Bằng chứng shared state

Chạy `configs/redis_cache.yaml` (cùng 3 scenario, `cache.backend: redis`) qua `scripts/run_chaos.py`; `reports/metrics_redis.json` cho `cache_hit_rate: 0.71` — các cache hit này đã được nhìn thấy xuyên suốt các lệnh gọi `FakeLLMProvider` từ một tiến trình `run_scenario()` duy nhất, tiến trình này tự tạo một instance `SharedRedisCache` dùng chung cho mọi request trong vòng lặp. Bằng chứng mạnh hơn cho việc chia sẻ *xuyên instance* là trực tiếp: sau khi chạy xong, một client `redis-cli` hoàn toàn mới (một tiến trình/kết nối khác, không phải tiến trình Python đã ghi dữ liệu) vẫn đọc lại được toàn bộ entry.

### Kết quả Redis CLI

```bash
$ docker compose exec redis redis-cli KEYS "rl:cache:*"
 1) "rl:cache:4fc3c69b9376"
 2) "rl:cache:3936614ac4c2"
 3) "rl:cache:d354658dc020"
 4) "rl:cache:9e413fd814eb"
 5) "rl:cache:3dab98c0e49e"
 6) "rl:cache:98332d0d1c9c"
 7) "rl:cache:fff10da1c72c"
 8) "rl:cache:734852f3cf4a"
 9) "rl:cache:0bc3b1acf73d"
10) "rl:cache:8baa2cfa11fa"
11) "rl:cache:dacb2b833659"
12) "rl:cache:095946136fea"
13) "rl:cache:844ef0143a5c"
```

13 câu hỏi khác nhau đã được cache trong Redis và vẫn đọc được từ một kết nối `redis-cli` hoàn toàn tách biệt — chứng minh state tồn tại độc lập với bất kỳ tiến trình gateway nào.

### So sánh latency: in-memory vs Redis

| Metric         | Cache in-memory | Cache Redis | Ghi chú                                                                                                                                                                              |
| -------------- | --------------: | ----------: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| latency_p50_ms |          277.08 |      277.39 | Gần như bằng nhau — round-trip Redis (container Docker local, <1ms) là không đáng kể so với latency provider mô phỏng ~200-280ms.                                         |
| latency_p95_ms |          316.09 |      315.48 | Gần như bằng nhau                                                                                                                                                                  |
| cache_hit_rate |            0.59 |        0.71 | Cao hơn ở lần chạy Redis — do biến thiên ngẫu nhiên giữa các lần chạy (`random.choice(queries)`), không phải do khác biệt backend (n=300/lần là mẫu khá nhỏ). |
| availability   |          0.9867 |        0.99 | Lần chạy Redis tình cờ đạt SLO 99%; cả hai đều nằm trong khoảng nhiễu bình thường giữa các lần chạy với mẫu 300 request.                                         |

## 7. Các kịch bản chaos (Chaos scenarios)

| Scenario            | Hành vi kỳ vọng                                                                                                                                                   | Hành vi quan sát được                                                                                                                                                                            | Pass/Fail |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| primary_timeout_100 | Primary fail 100% — mọi request phải route sang backup (hoặc static fallback nếu backup cũng fail), circuit của primary mở và giữ nguyên trạng thái mở | Breaker của primary mở ngay lập tức và giữ mở suốt scenario; gần như toàn bộ traffic được backup xử lý, thỉnh thoảng có`static_fallback` do backup tự fail (5%)                | Pass      |
| primary_flaky_50    | Primary fail ~50% — circuit phải dao động CLOSED/OPEN/HALF_OPEN khi các lần fail dồn lại rồi hết                                                           | `circuit_open_count` (10 trên tổng 3 scenario) chủ yếu đến từ scenario này; transition log cho thấy chu kỳ lặp lại `failure_threshold_reached` → `probe_failure`/`probe_success` | Pass      |
| all_healthy         | Cả hai provider đều khỏe (fail rate gốc: 25%/5%) — chủ yếu traffic đi qua primary, cache hấp thụ câu hỏi lặp, breaker hiếm khi mở                    | Đa số request đi route`primary` hoặc `cache_hit`, chỉ vài lần breaker trip do fail rate gốc 25% của primary                                                                              | Pass      |

## 7a. Kiểm thử tải đồng thời (Concurrent load test)

Chạy `scripts/concurrent_load_test.py` — cùng ngân sách 300 request như baseline tuần tự, nhưng gửi qua `ThreadPoolExecutor` với 10 worker chạy đồng thời thay vì từng request một.

| Metric             |                             Tuần tự (baseline) | Đồng thời (10 worker) |
| ------------------ | -----------------------------------------------: | -----------------------: |
| total_requests     |                                              300 |                      300 |
| availability       |                                           0.9867 |                     0.99 |
| cache_hit_rate     |                                             0.59 |                   0.6933 |
| static_fallbacks   |                                               ~4 |                        3 |
| latency_p50_ms     |                                           277.08 |                     0.43 |
| latency_p95_ms     |                                           316.09 |                   474.38 |
| latency_p99_ms     |                                            320.2 |                   508.67 |
| circuit_open_count |                                               10 |                        1 |
| throughput         | ~3.6 req/s (suy ra từ ~280ms/request tuần tự) |             109.78 req/s |

Throughput tăng khoảng 30 lần với 10 worker (109.78 so với ~3.6 req/s ngụ ý từ chạy tuần tự) — đúng như kỳ vọng, vì `FakeLLMProvider.complete()` dùng `sleep` (nhả GIL) thay vì tính toán CPU nặng, nên các lệnh gọi I/O-bound chồng lấn lên nhau tốt. Latency P50 gần như về 0 vì phần lớn request đồng thời rơi vào cache hit (0ms), chỉ xếp hàng sau một vài lệnh gọi provider đang chạy; ngược lại P95/P99 lại tăng thay vì giảm, vì khi cả 10 worker đều bận, một request có thể phải xếp hàng chờ hết một lệnh gọi provider dài 200-300ms trước khi bắt đầu được xử lý — hiệu ứng xếp hàng (queueing) mà lần chạy tuần tự không bao giờ gặp phải.

**Phát hiện về thread-safety:** `circuit_open_count` giảm từ 10 (tuần tự) xuống 1 (đồng thời) — không phải vì hệ thống khỏe hơn, mà vì các field của `CircuitBreaker` (`failure_count`, `state`, `success_count`) là thuộc tính dataclass thường, bị nhiều thread ghi/đọc mà không có khóa (lock). Khi chạy đồng thời, hai thread có thể cùng đọc `failure_count` trước khi thread nào ghi lại, âm thầm làm mất một lần tăng (lost-update race — lỗi kinh điển). Đây là một bug thật sự cho gateway đa luồng chạy production, không chỉ là hiện tượng của benchmark — xem thêm mục Phân tích lỗi.

## 8. Phân tích lỗi (Failure analysis)

**Điểm yếu còn tồn tại:** trong scenario `primary_timeout_100`, khi circuit của primary đã mở, mọi request phụ thuộc hoàn toàn vào backup. Backup có fail rate khác 0 (5%), và không có lớp fallback nào sau khi backup fail ngoài thông báo static — không có provider thứ ba hay chế độ "chỉ dùng cache" để hấp thụ các lần fail của backup. Đây chính là lý do availability tổng (98.67%) hụt nhẹ so với SLO 99%: nó bị chặn trên bởi `1 - fail_rate của backup` mỗi khi primary down hoàn toàn, không có lớp an toàn bổ sung nào khác.

**Đề xuất fix:** thêm chế độ suy giảm "cache-only" — khi tất cả breaker của các provider đều OPEN, thay vì trả ngay thông báo static, thử thêm một lượt `cache.get()` với ngưỡng similarity nới lỏng hơn (VD: 0.75) trước khi bỏ cuộc, vì một câu trả lời cache kém chính xác hơn một chút vẫn hữu ích cho người dùng hơn là một câu "dịch vụ đang suy giảm" cứng nhắc. Đánh đổi này là chấp nhận giảm một chút độ chính xác câu trả lời để đổi lấy availability khi toàn bộ provider đang gặp sự cố.

**Điểm yếu thứ hai (phát hiện khi kiểm thử tải đồng thời):** `CircuitBreaker.record_failure()` / `record_success()` không thread-safe — các luồng gọi đồng thời có thể race trên việc cập nhật `failure_count`/`state` (quan sát được: circuit_open_count giảm từ 10 lúc tuần tự xuống 1 lúc đồng thời dù cùng fail rate, cho thấy đây là lost-update chứ không phải hệ thống khỏe hơn thật). Một gateway đa luồng trong một tiến trình vì vậy có thể đếm thiếu số lần fail và không mở circuit đúng lúc cần. Cách fix: bọc 4 method thay đổi state trong một `threading.Lock` (rẻ, vì mỗi lệnh gọi rất ngắn), hoặc chuyển counter sang Redis với transaction atomic kiểu `INCR`/`WATCH` để đảm bảo an toàn thật sự khi chạy nhiều instance (cũng giải quyết luôn điểm ở mục Bước tiếp theo về việc chia sẻ state của breaker giữa các instance).

## 9. Bước tiếp theo (Next steps)

1. Thêm một lớp fallback thứ ba, rẻ hơn — "cache-only fallback" (xem trên) — để thu hẹp khoảng hụt availability trước khi nó vi phạm SLO.
2. Chuyển counter của circuit breaker sang Redis (`INCR`/`EXPIRE`) để state của breaker được chia sẻ giữa các instance gateway, không chỉ riêng cache — hiện tại hai instance có thể tự quyết định độc lập rằng primary khỏe/không khỏe và mâu thuẫn với nhau.
3. Thêm cost-aware routing: khi `estimated_cost` cộng dồn vượt ngưỡng ngân sách, bỏ qua provider primary đắt hơn và ưu tiên cache hit hoặc backup rẻ hơn, theo đúng stretch-goal trong README.
