# Stress Test Client - Hướng Dẫn Sử Dụng

## 🚀 Cải Thiện Chính

### ✅ **Memory Management**
- **Trước**: Load toàn bộ audio file vào memory cho mỗi process → Memory leak nghiêm trọng
- **Sau**: Streaming audio từ file → Tiết kiệm memory đáng kể

### ✅ **Cấu Hình Tối Ưu**
- **Trước**: MAX_CLIENTS = 50, TIME_INTERVAL = 15s
- **Sau**: MAX_CLIENTS = 200, TIME_INTERVAL = 60s

### ✅ **Duration Control**
- **Trước**: Không có kiểm soát thời gian
- **Sau**: Hỗ trợ duration control cho audio dài

### ✅ **Resource Monitoring**
- **Trước**: Monitoring cơ bản
- **Sau**: Memory warning, network throughput, chi tiết hơn

## 📋 Cách Sử Dụng

### 1. **Single Client Test**
```bash
# Test 1 client trong 60 giây
python test_client1.py single 60

# Test 1 client trong 15 phút
python test_client1.py single 900
```

### 2. **Single Process Test**
```bash
# Test 1 process với 5 clients trong 60 giây
python test_client1.py process 1 5 60

# Test 1 process với 10 clients trong 15 phút
python test_client1.py process 1 10 900
```

### 3. **Full Stress Test**
```bash
# Stress test với duration mặc định (15 phút)
python test_client1.py stress

# Stress test với duration tùy chỉnh
python test_client1.py stress 1800  # 30 phút
```

### 4. **Default Mode**
```bash
# Chạy stress test với cấu hình mặc định
python test_client1.py
```

## ⚙️ Cấu Hình

### Audio Configuration
```python
CHUNK = 320          # Audio chunk size
RATE = 16000         # Sample rate
CHANNELS = 1         # Mono audio
DTYPE = 'int16'      # 16-bit audio
```

### Test Configuration
```python
INITIAL_CLIENTS = 10      # Bắt đầu với 10 clients
MAX_CLIENTS = 200         # Tối đa 200 clients
CLIENT_INCREMENT = 20     # Tăng 20 clients mỗi lần
TIME_INTERVAL = 60        # Chạy 60 giây mỗi level
CLIENTS_PER_PROCESS = 20  # 20 clients per process
```

### Server Configuration
```python
SERVER_URL = "ws://localhost:8000"
SESSION_ID = "stress_test_room"
TRANSCRIPT = True
TRANSLATION = True
AUDIO_FILE_PATH = "test_audio.wav"  # Đường dẫn file audio
```

## 📊 Monitoring

### System Metrics
- **CPU Usage**: Theo dõi CPU utilization
- **Memory Usage**: Cảnh báo khi memory > 85%
- **Network Throughput**: Bytes sent/received per interval
- **Process Memory**: Memory usage của stress test process

### Client Metrics
- **Active Clients**: Số clients đang hoạt động
- **Total Clients**: Tổng số clients đã tạo
- **Chunks Sent**: Tổng số audio chunks đã gửi
- **Errors**: Số lỗi xảy ra
- **Processes**: Số processes đang chạy

## 🔧 Troubleshooting

### Memory Issues
- **Triệu chứng**: Memory usage tăng cao, system chậm
- **Giải pháp**: Kiểm tra audio file size, giảm MAX_CLIENTS

### Connection Issues
- **Triệu chứng**: Clients không kết nối được
- **Giải pháp**: Kiểm tra server status, network connectivity

### Performance Issues
- **Triệu chứng**: CPU usage cao, response chậm
- **Giải pháp**: Giảm CLIENT_INCREMENT, tăng TIME_INTERVAL

## 📁 Log Files

### Main Logs
- `stress_test_main.log`: Log chính của stress test
- `stress_test_p{id}.log`: Log của từng process

### Directory Structure
```
stress_test_logs/
├── responses/     # Response logs
├── stats/         # Statistics logs
└── errors/        # Error logs
```

## 🎯 Best Practices

### 1. **Audio File**
- Sử dụng audio file có độ dài phù hợp (1-15 phút)
- Format: WAV, 16kHz, mono
- Kích thước: < 100MB để tránh memory issues

### 2. **System Resources**
- Đảm bảo đủ RAM (ít nhất 8GB)
- CPU: Multi-core recommended
- Network: Stable connection

### 3. **Testing Strategy**
- Bắt đầu với single client test
- Tăng dần số clients
- Monitor system resources
- Dừng khi có dấu hiệu bất thường

## 🚨 Lưu Ý Quan Trọng

1. **Memory Management**: File đã được tối ưu để tránh memory leak
2. **Duration Control**: Luôn set duration để tránh chạy vô hạn
3. **Resource Monitoring**: Theo dõi system metrics trong quá trình test
4. **Graceful Shutdown**: Sử dụng Ctrl+C để dừng test an toàn

## 📈 Expected Results

Với audio 15 phút và cấu hình mới:
- **Memory Usage**: < 2GB (thay vì 3.6GB+)
- **Max Clients**: Có thể đạt 200+ clients
- **Stability**: Ít crash hơn, ổn định hơn
- **Performance**: Tốt hơn với streaming audio
