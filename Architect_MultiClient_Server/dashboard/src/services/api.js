import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  }
});

// Flag to prevent infinite refresh loops
let isRefreshing = false;
let failedQueue = [];

const processQueue = (error, token = null) => {
  failedQueue.forEach(prom => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token);
    }
  });

  failedQueue = [];
};

// Request interceptor to add JWT token to all requests
apiClient.interceptors.request.use(
  (config) => {
    // Get token from localStorage
    const authData = localStorage.getItem('auth');

    if (authData) {
      try {
        const { token } = JSON.parse(authData);
        if (token) {
          config.headers['Authorization'] = `Bearer ${token}`;
        }
      } catch (err) {
        console.error('Failed to parse auth data:', err);
      }
    }

    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Response interceptor to handle authentication errors and auto-refresh
apiClient.interceptors.response.use(
  (response) => {
    return response;
  },
  async (error) => {
    const originalRequest = error.config;

    // Handle 401 Unauthorized
    if (error.response?.status === 401 && !originalRequest._retry) {
      // Don't try to refresh on login/refresh endpoints
      if (originalRequest.url?.includes('/auth/mezon/exchange') ||
          originalRequest.url?.includes('/auth/mezon/refresh')) {
        return Promise.reject(error);
      }

      if (isRefreshing) {
        // Already refreshing, queue this request
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        })
          .then(token => {
            originalRequest.headers['Authorization'] = `Bearer ${token}`;
            return apiClient(originalRequest);
          })
          .catch(err => {
            return Promise.reject(err);
          });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      const authData = localStorage.getItem('auth');
      let refreshToken = null;

      if (authData) {
        try {
          const parsed = JSON.parse(authData);
          refreshToken = parsed.refreshToken;
        } catch (err) {
          console.error('Failed to parse auth data:', err);
        }
      }

      if (!refreshToken) {
        // No refresh token, redirect to login
        isRefreshing = false;
        localStorage.removeItem('auth');

        if (!window.location.pathname.startsWith('/login') &&
            !window.location.pathname.startsWith('/callback')) {
          window.location.href = '/login';
        }

        return Promise.reject(error);
      }

      try {
        // Try to refresh the access token
        const response = await axios.post(
          `${API_BASE_URL}/api/auth/mezon/refresh`,
          { refresh_token: refreshToken },
          { headers: { 'Content-Type': 'application/json' } }
        );

        if (response.data && response.data.access_token) {
          const newAccessToken = response.data.access_token;

          // Update stored token
          localStorage.setItem('auth', JSON.stringify({
            token: newAccessToken,
            refreshToken: refreshToken
          }));

          // Update authorization header
          apiClient.defaults.headers.common['Authorization'] = `Bearer ${newAccessToken}`;
          originalRequest.headers['Authorization'] = `Bearer ${newAccessToken}`;

          // Process queued requests
          processQueue(null, newAccessToken);

          isRefreshing = false;

          // Retry original request
          return apiClient(originalRequest);
        } else {
          throw new Error('No access token in refresh response');
        }
      } catch (refreshError) {
        // Refresh failed, clear tokens and redirect to login
        processQueue(refreshError, null);
        isRefreshing = false;

        localStorage.removeItem('auth');

        if (!window.location.pathname.startsWith('/login') &&
            !window.location.pathname.startsWith('/callback')) {
          window.location.href = '/login';
        }

        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

// Room APIs
export const getRooms = async (params = {}) => {
  const { limit = 20, skip = 0, status = null, search = null, from_utc = null, to_utc = null } = params;
  const queryParams = new URLSearchParams();
  queryParams.set('limit', String(limit));
  queryParams.set('skip', String(skip));
  if (status) queryParams.set('status', status);
  if (search && search.trim()) queryParams.set('search', search.trim());
  if (from_utc) queryParams.set('from_utc', from_utc);
  if (to_utc) queryParams.set('to_utc', to_utc);

  const response = await apiClient.get(`/api/transcripts/rooms?${queryParams.toString()}`);
  return response.data;
};

export const getRoomByName = async (roomName) => {
  const response = await apiClient.get(`/api/transcripts/rooms/${roomName}`);
  return response.data;
};

export const getRoomById = async (roomId) => {
  const response = await apiClient.get(`/api/transcripts/rooms/id/${roomId}`);
  return response.data;
};

export const getRoomStatistics = async (roomName) => {
  const response = await apiClient.get(`/api/transcripts/rooms/${roomName}/statistics`);
  return response.data;
};

export const getRoomStatisticsById = async (roomId) => {
  const response = await apiClient.get(`/api/transcripts/rooms/id/${roomId}/statistics`);
  return response.data;
};

// Summary APIs
export const getSummaryByRoom = async (roomName, startTime = null, endTime = null) => {
  const queryParams = new URLSearchParams();
  if (startTime) queryParams.append('start_time', startTime);
  if (endTime) queryParams.append('end_time', endTime);

  const query = queryParams.toString();
  const url = `/api/summary/room/${roomName}${query ? `?${query}` : ''}`;
  const response = await apiClient.get(url);
  return response.data;
};

export const getSummaryByRoomId = async (roomId) => {
  const response = await apiClient.get(`/api/summary/room/id/${roomId}`);
  return response.data;
};

// Transcript APIs
export const getFullTranscript = async (trackId) => {
  const response = await apiClient.get(`/api/transcripts/tracks/${trackId}/transcript`);
  return response.data;
};

export const getChunksByTrack = async (trackId, params = {}) => {
  const { limit = 100, skip = 0, sorted_by_index = true } = params;
  const queryParams = new URLSearchParams({
    limit,
    skip,
    sorted_by_index
  });

  const response = await apiClient.get(`/api/transcripts/tracks/${trackId}/chunks?${queryParams}`);
  return response.data;
};

export default apiClient;
