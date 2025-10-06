# Mezon Call Translation - Monitoring Stack

Hệ thống monitoring độc lập sử dụng Prometheus + Grafana + AlertManager để giám sát Mezon Call Translation Service.

## 📋 Mục lục

- [Tổng quan](#tổng-quan)
- [Kiến trúc](#kiến-trúc)
- [Cài đặt](#cài-đặt)
- [Sử dụng](#sử-dụng)
- [Cấu hình](#cấu-hình)
- [Dashboards](#dashboards)
- [Alerts](#alerts)
- [Troubleshooting](#troubleshooting)

## 🎯 Tổng quan

Stack monitoring này bao gồm:

- **Prometheus**: Thu thập và lưu trữ metrics
- **Grafana**: Visualization và dashboards
- **AlertManager**: Quản lý và gửi alerts

## 🏗️ Kiến trúc

```
┌─────────────────────────────────────────────────────────┐
│                  Monitoring Stack                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Prometheus  │  │   Grafana    │  │ AlertManager │  │
│  │   :9090      │  │    :3000     │  │    :9093     │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                  │                  │          │
│         │ scrape           │ query            │ alerts   │
│         │                  │                  │          │
└─────────┼──────────────────┼──────────────────┼──────────┘
          │                  │                  │
          │                  │                  │
┌─────────▼──────────────────▼──────────────────▼──────────┐
│              Mezon Call Translation Service               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │    Nginx     │  │   Server 1   │  │   Server 2   │  │
│  │   :8000      │  │   :8000      │  │   :8000      │  │
│  │  /metrics    │  │  /metrics    │  │  /metrics    │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└───────────────────────────────────────────────────────────┘
```

## 🚀 Cài đặt

### Bước 1: Chuẩn bị môi trường

```bash
# Di chuyển vào thư mục monitoring
cd monitoring

# Copy file .env.example thành .env
cp .env.example .env

# Chỉnh sửa .env với thông tin của bạn
nano .env
```

### Bước 2: Khởi động Mezon Service trước

```bash
# Quay lại thư mục gốc
cd ..

# Khởi động Mezon service
docker-compose up -d

# Kiểm tra service đã chạy
docker-compose ps

# Kiểm tra metrics endpoint
curl http://localhost:8000/metrics
```

### Bước 3: Khởi động Monitoring Stack

```bash
# Di chuyển vào thư mục monitoring
cd monitoring

# Khởi động monitoring stack
docker-compose up -d

# Kiểm tra các container
docker-compose ps
```

### Bước 4: Xác minh cài đặt

```bash
# Kiểm tra Prometheus
curl http://localhost:9090/-/healthy

# Kiểm tra Grafana
curl http://localhost:3000/api/health

# Kiểm tra AlertManager
curl http://localhost:9093/-/healthy
```

## 📊 Sử dụng

### Truy cập các dịch vụ

| Service | URL | Credentials |
|---------|-----|-------------|
| Prometheus | http://localhost:9090 | N/A |
| Grafana | http://localhost:3000 | admin / admin (default) |
| AlertManager | http://localhost:9093 | N/A |

### Grafana Dashboard

1. Truy cập Grafana: http://localhost:3000
2. Đăng nhập với credentials (mặc định: admin/admin)
3. Vào **Dashboards** → **Browse**
4. Chọn folder **Mezon Call Translation**
5. Mở dashboard **Mezon Call Translation - Overview**

### Prometheus Queries

Truy cập Prometheus UI: http://localhost:9090

**Ví dụ queries:**

```promql
# WebSocket connections
ws_connections_current

# HTTP request rate
rate(http_requests_total[5m])

# Transcription duration p95
histogram_quantile(0.95, rate(transcription_duration_seconds_bucket[5m]))

# Error rate
rate(ws_errors_total[5m])

# CPU usage
cpu_usage_percent

# Memory usage
memory_usage_bytes
```

## ⚙️ Cấu hình

### Prometheus Configuration

File: `prometheus/prometheus.yml`

**Scrape interval:**
```yaml
global:
  scrape_interval: 15s  # Thay đổi nếu cần
```

**Thêm target mới:**
```yaml
scrape_configs:
  - job_name: 'my-service'
    static_configs:
      - targets: ['my-service:8080']
```

### Grafana Configuration

**Thay đổi admin password:**

1. Sửa file `.env`:
```bash
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=your-secure-password
```

2. Restart Grafana:
```bash
docker-compose restart grafana
```

**Thêm datasource mới:**

Tạo file trong `grafana/provisioning/datasources/`:
```yaml
apiVersion: 1
datasources:
  - name: My Datasource
    type: prometheus
    url: http://my-prometheus:9090
```

### AlertManager Configuration

File: `alertmanager/alertmanager.yml`

**Cấu hình Email alerts:**

```yaml
global:
  smtp_smarthost: 'smtp.gmail.com:587'
  smtp_from: 'alerts@example.com'
  smtp_auth_username: 'your-email@gmail.com'
  smtp_auth_password: 'your-app-password'

receivers:
  - name: 'email-alerts'
    email_configs:
      - to: 'team@example.com'
```

**Cấu hình Slack alerts:**

```yaml
receivers:
  - name: 'slack-alerts'
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/YOUR/WEBHOOK/URL'
        channel: '#alerts'
```

## 📈 Dashboards

### Mezon Overview Dashboard

Dashboard chính hiển thị:

- **WebSocket Connections**: Số kết nối hiện tại
- **HTTP Request Rate**: Tốc độ request
- **CPU & Memory Usage**: Sử dụng tài nguyên
- **Transcription Rate**: Tốc độ xử lý transcription
- **Processing Duration**: Thời gian xử lý (p95)
- **Error Rates**: Tỷ lệ lỗi
- **Queue Size**: Kích thước queue
- **Circuit Breaker State**: Trạng thái circuit breaker

### Tạo Dashboard mới

1. Vào Grafana → **Dashboards** → **New Dashboard**
2. Add Panel
3. Chọn Prometheus datasource
4. Nhập query
5. Cấu hình visualization
6. Save dashboard

**Hoặc import dashboard:**

1. Vào **Dashboards** → **Import**
2. Upload JSON file hoặc paste JSON
3. Chọn Prometheus datasource
4. Import

## 🚨 Alerts

### Alert Rules

File: `prometheus/alerts.yml`

**Các alerts đã cấu hình:**

| Alert | Condition | Severity |
|-------|-----------|----------|
| HighErrorRate | Error rate > 5% for 5m | warning |
| HighWebSocketErrors | WS error rate > 0.1/s for 5m | warning |
| HighMemoryUsage | Memory > 2GB for 10m | warning |
| HighCPUUsage | CPU > 80% for 10m | warning |
| CircuitBreakerOpen | Circuit breaker open for 2m | critical |
| HighQueueSize | Queue size > 50 for 5m | warning |
| SlowTranscription | p95 > 5s for 5m | warning |
| ServiceDown | Service down for 2m | critical |

### Thêm Alert Rule mới

Thêm vào `prometheus/alerts.yml`:

```yaml
groups:
  - name: my_alerts
    rules:
      - alert: MyAlert
        expr: my_metric > 100
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "My alert summary"
          description: "My alert description"
```

Reload Prometheus:
```bash
curl -X POST http://localhost:9090/-/reload
```

### Kiểm tra Alerts

1. Prometheus UI: http://localhost:9090/alerts
2. AlertManager UI: http://localhost:9093

## ⚠️ Quan trọng: Scraping Strategy

### Tại sao KHÔNG scrape qua Nginx?

**Vấn đề:** Khi scrape metrics qua Nginx load balancer, Prometheus chỉ lấy được metrics từ **1 server instance** mỗi lần (do round-robin), không phải tất cả instances.

```
❌ SAI: Prometheus → Nginx → Server (random 1 instance)
✅ ĐÚNG: Prometheus → Server 1, Server 2, Server 3... (tất cả instances)
```

**Giải pháp hiện tại:**
- Prometheus scrape **trực tiếp** từng server instance
- Sử dụng Docker DNS để discover tất cả instances
- Thu thập metrics đầy đủ từ mọi instance

**Chi tiết:** Xem [SCRAPING-STRATEGY.md](docs/SCRAPING-STRATEGY.md)

### Kiểm tra targets

```bash
# Windows
.\scripts\check-targets.ps1

# Linux/Mac
./scripts/check-targets.sh
```

## 🔧 Troubleshooting

### Prometheus không scrape được metrics

**Kiểm tra:**

```bash
# Kiểm tra Mezon service đang chạy
docker ps | grep mezon

# Kiểm tra metrics endpoint
curl http://localhost:8000/metrics

# Kiểm tra Prometheus targets
curl http://localhost:9090/api/v1/targets
```

**Giải pháp:**

1. Đảm bảo Mezon service đã khởi động
2. Kiểm tra network connectivity
3. Xem logs: `docker-compose logs prometheus`

### Grafana không kết nối được Prometheus

**Kiểm tra:**

```bash
# Kiểm tra Prometheus đang chạy
curl http://localhost:9090/-/healthy

# Kiểm tra từ Grafana container
docker exec mezon-grafana curl http://prometheus:9090/-/healthy
```

**Giải pháp:**

1. Restart Grafana: `docker-compose restart grafana`
2. Kiểm tra datasource configuration
3. Xem logs: `docker-compose logs grafana`

### AlertManager không gửi alerts

**Kiểm tra:**

```bash
# Kiểm tra AlertManager config
docker exec mezon-alertmanager amtool check-config /etc/alertmanager/alertmanager.yml

# Xem alerts đang active
curl http://localhost:9093/api/v2/alerts
```

**Giải pháp:**

1. Kiểm tra cấu hình receiver
2. Test webhook/email configuration
3. Xem logs: `docker-compose logs alertmanager`

### Container không khởi động

**Kiểm tra logs:**

```bash
# Xem logs của tất cả services
docker-compose logs

# Xem logs của service cụ thể
docker-compose logs prometheus
docker-compose logs grafana
docker-compose logs alertmanager
```

**Giải pháp:**

1. Kiểm tra file cấu hình syntax
2. Kiểm tra ports không bị conflict
3. Kiểm tra volumes permissions
4. Restart: `docker-compose restart`

### Metrics không hiển thị trong Grafana

**Kiểm tra:**

1. Prometheus có scrape được metrics không?
   - Vào Prometheus UI → Status → Targets
2. Query trực tiếp trong Prometheus
   - Vào Prometheus UI �� Graph
   - Nhập query và Execute
3. Kiểm tra time range trong Grafana
4. Kiểm tra datasource trong panel

## 🛠️ Maintenance

### Backup

**Backup Grafana dashboards:**
```bash
# Export tất cả dashboards
docker exec mezon-grafana grafana-cli admin export-dashboard > backup.json
```

**Backup Prometheus data:**
```bash
# Backup volume
docker run --rm -v monitoring_prometheus-data:/data -v $(pwd):/backup alpine tar czf /backup/prometheus-backup.tar.gz /data
```

### Update

**Update images:**
```bash
docker-compose pull
docker-compose up -d
```

### Cleanup

**Xóa old data:**
```bash
# Stop services
docker-compose down

# Remove volumes (WARNING: This deletes all data)
docker volume rm monitoring_prometheus-data
docker volume rm monitoring_grafana-data
docker volume rm monitoring_alertmanager-data

# Start fresh
docker-compose up -d
```

## 📚 Tài liệu tham khảo

- [Prometheus Documentation](https://prometheus.io/docs/)
- [Grafana Documentation](https://grafana.com/docs/)
- [AlertManager Documentation](https://prometheus.io/docs/alerting/latest/alertmanager/)
- [PromQL Cheat Sheet](https://promlabs.com/promql-cheat-sheet/)

## 🤝 Support

Nếu gặp vấn đề, vui lòng:

1. Kiểm tra logs: `docker-compose logs`
2. Xem Troubleshooting section
3. Tạo issue với logs và mô tả chi tiết

## 📝 License

MIT License
