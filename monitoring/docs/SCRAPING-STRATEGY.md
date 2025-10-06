# Prometheus Scraping Strategy cho Multi-Instance Setup

## 🎯 Vấn đề

Khi sử dụng Nginx load balancer với nhiều server instances, việc scrape metrics qua Nginx gặp vấn đề:

### ❌ **Scraping qua Nginx (SAI)**

```
Prometheus → Nginx → Server Instance (random)
```

**Vấn đề:**
- Mỗi lần scrape, Nginx chỉ route đến **1 server instance** (round-robin)
- Prometheus không thu thập được metrics từ **TẤT CẢ** instances
- Metrics bị thiếu và không chính xác
- Không thể monitor từng instance riêng biệt

**Ví dụ:**
```
Scrape 1: Prometheus → Nginx → Server 1 ✅
Scrape 2: Prometheus → Nginx → Server 2 ✅
Scrape 3: Prometheus → Nginx → Server 1 ✅
→ Metrics của Server 3, 4, 5... bị bỏ qua!
```

### ✅ **Scraping trực tiếp (ĐÚNG)**

```
Prometheus → Server Instance 1
          → Server Instance 2
          → Server Instance 3
          → ...
```

**Ưu điểm:**
- Thu thập metrics từ **TẤT CẢ** instances
- Monitor từng instance riêng biệt
- Metrics chính xác và đầy đủ
- Có thể aggregate hoặc xem per-instance

---

## 🔧 Giải pháp đã implement

### **Direct Container Scraping (Docker Compose)**

```yaml
- job_name: 'mezon-server-instances'
  metrics_path: '/metrics'
  static_configs:
    # Server instance 1
    - targets: ['mezon-call-translation-server-1:8000']
      labels:
        service: 'mezon-server'
        component: 'backend'
        instance: 'server-1'
    # Server instance 2
    - targets: ['mezon-call-translation-server-2:8000']
      labels:
        service: 'mezon-server'
        component: 'backend'
        instance: 'server-2'
    # Server instance 3
    - targets: ['mezon-call-translation-server-3:8000']
      labels:
        service: 'mezon-server'
        component: 'backend'
        instance: 'server-3'
```

**Cách hoạt động:**
- Docker Compose tạo containers với tên predictable: `<project>-<service>-<number>`
- Ví dụ: `mezon-call-translation-server-1`, `mezon-call-translation-server-2`, etc.
- Prometheus scrape trực tiếp từng container bằng tên
- Mỗi instance được monitor riêng biệt với label `instance`
- Thu thập metrics từ **TẤT CẢ** instances đồng thời (không phải round-robin)

**Ưu điểm:**
- ✅ Không cần Docker Swarm
- ✅ Hoạt động hoàn hảo với `docker-compose`
- ✅ Scrape tất cả instances cùng lúc
- ✅ Monitor per-instance chính xác 100%
- ✅ Đơn giản, dễ hiểu và maintain

**Lưu ý:**
- Khi scale thêm instances, cần thêm targets vào config
- Container names phải match với format Docker Compose
- Project name mặc định là tên thư mục (có thể override với `COMPOSE_PROJECT_NAME`)

---

## 📊 So sánh các phương pháp

| Phương pháp | Pros | Cons | Use Case |
|-------------|------|------|----------|
| **Qua Nginx** | - Đơn giản<br>- 1 endpoint duy nhất | - Chỉ scrape 1 instance/lần<br>- Metrics không đầy đủ<br>- Không monitor per-instance | ❌ **KHÔNG NÊN DÙNG** |
| **Direct Container Names** | - Scrape tất cả instances<br>- Chính xác 100%<br>- Không cần Swarm<br>- Dễ debug | - Cần update config khi scale<br>- Manual configuration | ✅ **ĐANG DÙNG** - Docker Compose |
| **DNS Service Discovery** | - Auto-discover instances<br>- Real-time updates<br>- Không cần update config | - Chỉ hoạt động với Docker Swarm<br>- Phức tạp hơn | Production với Swarm |
| **Round-robin DNS** | - Đơn giản<br>- Không cần Swarm | - Không scrape đồng thời<br>- Phụ thuộc vào timing | ⚠️ Không đáng tin cậy |

---

## 🚀 Setup Instructions

### Current Setup (Docker Compose)

**1. Kiểm tra số lượng server instances:**
```bash
docker ps --filter "name=mezon-call-translation.*server"
```

**2. Cấu hình Prometheus (`prometheus/prometheus.yml`):**
```yaml
- job_name: 'mezon-server-instances'
  metrics_path: '/metrics'
  static_configs:
    - targets: ['mezon-call-translation-server-1:8000']
      labels:
        service: 'mezon-server'
        component: 'backend'
        instance: 'server-1'
    - targets: ['mezon-call-translation-server-2:8000']
      labels:
        service: 'mezon-server'
        component: 'backend'
        instance: 'server-2'
    - targets: ['mezon-call-translation-server-3:8000']
      labels:
        service: 'mezon-server'
        component: 'backend'
        instance: 'server-3'
```

**3. Reload Prometheus:**
```bash
# Windows PowerShell
Invoke-WebRequest -Uri http://localhost:9090/-/reload -Method POST

# Linux/Mac
curl -X POST http://localhost:9090/-/reload
```

**4. Verify targets:**
```bash
# Windows
.\scripts\check-targets.ps1

# Linux/Mac
./scripts/check-targets.sh
```

### Scaling Up

**Khi scale thêm instances:**

```bash
# Scale to 5 instances
docker-compose up -d --scale server=5
```

**Cập nhật Prometheus config:**
```yaml
# Thêm vào prometheus.yml
- targets: ['mezon-call-translation-server-4:8000']
  labels:
    service: 'mezon-server'
    component: 'backend'
    instance: 'server-4'
- targets: ['mezon-call-translation-server-5:8000']
  labels:
    service: 'mezon-server'
    component: 'backend'
    instance: 'server-5'
```

**Reload Prometheus:**
```bash
Invoke-WebRequest -Uri http://localhost:9090/-/reload -Method POST
```

---

## 🔍 Verification

### 1. Check Prometheus Targets

**Web UI:**
```
http://localhost:9090/targets
```

Bạn sẽ thấy tất cả instances với status "UP":
```
mezon-server-instances  mezon-call-translation-server-1:8000  UP
mezon-server-instances  mezon-call-translation-server-2:8000  UP
mezon-server-instances  mezon-call-translation-server-3:8000  UP
```

**API:**
```bash
curl http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="mezon-server-instances") | {instance: .labels.instance, health: .health}'
```

### 2. Check Metrics Coverage

**Query tất cả instances:**
```promql
up{job="mezon-server-instances"}
```

**Expected result:**
```
up{instance="server-1", job="mezon-server-instances"} 1
up{instance="server-2", job="mezon-server-instances"} 1
up{instance="server-3", job="mezon-server-instances"} 1
```

### 3. Check Metrics Aggregation

**Total requests across all instances:**
```promql
sum(rate(http_requests_total{job="mezon-server-instances"}[5m]))
```

**Per-instance requests:**
```promql
rate(http_requests_total{job="mezon-server-instances"}[5m])
```

**Top instances by load:**
```promql
topk(3, rate(http_requests_total{job="mezon-server-instances"}[5m]))
```

### 4. Run Check Script

```bash
# Windows
cd monitoring
.\scripts\check-targets.ps1

# Linux/Mac
cd monitoring
./scripts/check-targets.sh
```

---

## 🐛 Troubleshooting

### Issue: "No such host" hoặc "Connection refused"

**Nguyên nhân:**
- Container name không đúng
- Prometheus không trong cùng network

**Giải pháp:**
```bash
# 1. Check container names
docker ps --filter "name=server" --format "{{.Names}}"

# 2. Check Prometheus network
docker inspect mezon-prometheus | jq '.[0].NetworkSettings.Networks'

# 3. Ensure monitoring stack connects to mezon-network
# In monitoring/docker-compose.yml:
networks:
  mezon-network:
    external: true
    name: mezon-call-translation_mezon-network
```

### Issue: Chỉ thấy 1 instance trong targets

**Nguyên nhân:**
- Chỉ có 1 server instance đang chạy
- Config chưa được reload

**Giải pháp:**
```bash
# 1. Check số lượng server instances
docker ps | grep server

# 2. Scale up nếu cần
cd ..  # về thư mục gốc
docker-compose up -d --scale server=3

# 3. Reload Prometheus
Invoke-WebRequest -Uri http://localhost:9090/-/reload -Method POST
```

### Issue: Target status "DOWN"

**Nguyên nhân:**
- Container không healthy
- Metrics endpoint không accessible

**Giải pháp:**
```bash
# 1. Check container health
docker ps --filter "name=server"

# 2. Test metrics endpoint
docker exec mezon-call-translation-server-1 curl http://localhost:8000/metrics

# 3. Check logs
docker logs mezon-call-translation-server-1
```

### Issue: Metrics không aggregate đúng

**Nguyên nhân:**
- Labels không consistent

**Giải pháp:**
```yaml
# Ensure all targets have same labels structure
labels:
  service: 'mezon-server'      # Same for all
  component: 'backend'          # Same for all
  instance: 'server-1'          # Unique per instance
```

---

## 📈 Best Practices

### 1. **Labeling Strategy**

```yaml
labels:
  service: 'mezon-server'      # Service name (same for all instances)
  component: 'backend'          # Component type (same for all instances)
  instance: 'server-1'          # Unique instance identifier
  environment: 'production'     # Environment (optional)
```

### 2. **Scrape Interval**

```yaml
global:
  scrape_interval: 15s  # Balance between freshness and load
```

**Recommendations:**
- Development: 15-30s
- Production: 10-15s
- High-frequency: 5-10s (careful with load)

### 3. **Aggregation Queries**

**Total across all instances:**
```promql
sum(rate(http_requests_total{job="mezon-server-instances"}[5m])) by (method, endpoint)
```

**Per-instance breakdown:**
```promql
rate(http_requests_total{job="mezon-server-instances"}[5m])
```

**Compare instances:**
```promql
rate(http_requests_total{job="mezon-server-instances"}[5m]) > 0
```

### 4. **Alerting**

**Alert on any instance down:**
```yaml
- alert: ServerInstanceDown
  expr: up{job="mezon-server-instances"} == 0
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "Server instance {{ $labels.instance }} is down"
    description: "Instance {{ $labels.instance }} has been down for more than 2 minutes"
```

**Alert on aggregate metrics:**
```yaml
- alert: HighTotalErrorRate
  expr: sum(rate(http_requests_total{job="mezon-server-instances",status=~"5.."}[5m])) > 10
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "High error rate across all instances"
    description: "Total error rate is {{ $value }} errors/sec"
```

---

## 🎓 Summary

### ✅ **Current Implementation:**
- ✅ Scraping trực tiếp từng server instance bằng container name
- ✅ Hoạt động hoàn hảo với Docker Compose
- ✅ Monitor per-instance chính xác
- ✅ Thu thập metrics từ TẤT CẢ instances đồng thời
- ✅ Proper labeling cho aggregation

### 📝 **Maintenance Checklist:**
- [ ] Khi scale up: Thêm targets vào `prometheus.yml`
- [ ] Sau khi update config: Reload Prometheus
- [ ] Định kỳ: Chạy `check-targets.ps1` để verify
- [ ] Monitor: Check Grafana dashboards cho anomalies

### 🎯 **Key Takeaways:**
- **KHÔNG** scrape qua load balancer
- **LUÔN** scrape trực tiếp từng instance
- **SỬ DỤNG** container names với Docker Compose
- **VERIFY** targets sau mỗi lần scale

---

## 📚 References

- [Prometheus Configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
- [Docker Compose Networking](https://docs.docker.com/compose/networking/)
- [Prometheus Best Practices](https://prometheus.io/docs/practices/naming/)
- [Docker Container Names](https://docs.docker.com/compose/compose-file/#container_name)
