import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
const AUDIO_BASE_URL = import.meta.env.VITE_AUDIO_BASE_URL;

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  }
});

let refreshHandler = null;

export const setAuthRefreshHandler = (handler) => {
  refreshHandler = handler;
};

// Auth APIs
export const getCurrentUser = async (accessToken) => {
  const headers = accessToken ? { 'Authorization': `Bearer ${accessToken}` } : undefined;
  const response = await apiClient.get('/api/v2/auth/mezon/userinfo', { headers });
  return response.data;
};

export const refreshAuthTokens = async (refreshToken) => {
  if (!refreshToken) {
    throw new Error('Missing refresh token');
  }

  const response = await apiClient.post('/api/v2/auth/refresh', {
    refresh_token: refreshToken
  });

  return response.data;
};

export const logoutSession = async (accessToken, refreshToken) => {
  if (!accessToken || !refreshToken) {
    throw new Error('Missing access token or refresh token');
  }

  const response = await apiClient.post(
    '/api/v2/auth/logout',
    { refresh_token: refreshToken },
    { headers: { 'Authorization': `Bearer ${accessToken}` } }
  );

  return response.data;
};

// Request interceptor to add JWT token to all requests
apiClient.interceptors.request.use(
  (config) => {
    // Get token from localStorage
    const authData = localStorage.getItem('auth');

    if (authData) {
      try {
        const { accessToken } = JSON.parse(authData);
        if (accessToken) {
          config.headers['Authorization'] = `Bearer ${accessToken}`;
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

      originalRequest._retry = true;

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

      if (!refreshToken || !refreshHandler) {
        // No refresh token or handler, redirect to login
        localStorage.removeItem('auth');

        if (!window.location.pathname.startsWith('/login') &&
          !window.location.pathname.startsWith('/callback')) {
          window.location.href = '/login';
        }

        return Promise.reject(error);
      }

      try {
        const refreshed = await refreshHandler();
        if (!refreshed?.accessToken) {
          throw new Error('Refresh token failed');
        }

        apiClient.defaults.headers.common['Authorization'] = `Bearer ${refreshed.accessToken}`;
        originalRequest.headers['Authorization'] = `Bearer ${refreshed.accessToken}`;

        return apiClient(originalRequest);
      } catch (refreshError) {
        // Refresh failed, clear tokens and redirect to login
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

  const response = await apiClient.get(`/api/v2/rooms?${queryParams.toString()}`);
  return response.data;
};

export const getRoomByName = async (roomName) => {
  const response = await apiClient.get(`/api/v2/rooms/${roomName}`);
  return response.data;
};

export const getRoomById = async (roomId) => {
  const response = await apiClient.get(`/api/v2/rooms/id/${roomId}`);
  return response.data;
};

export const getRoomStatistics = async (roomName) => {
  const response = await apiClient.get(`/api/v2/rooms/${roomName}/statistics`);
  return response.data;
};

export const getRoomStatisticsById = async (roomId) => {
  const response = await apiClient.get(`/api/v2/rooms/id/${roomId}/statistics`);
  return response.data;
};

export const getRoomAudioInfoById = async (roomId) => {
  const response = await apiClient.get(`/api/v2/rooms/audio_info/${roomId}`);
  return response.data;
};

export const buildAudioUrl = (filename) => {
  if (!filename) {
    return '';
  }

  const normalizedBase = AUDIO_BASE_URL.endsWith('/') ? AUDIO_BASE_URL : `${AUDIO_BASE_URL}/`;
  const normalizedPath = filename
    .split('/')
    .filter(Boolean)
    .map(segment => encodeURIComponent(segment))
    .join('/');

  return new URL(normalizedPath, normalizedBase).toString();
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
  const response = await apiClient.get(`/api/v2/summary/room/id/${roomId}`);
  return response.data;
};

export default apiClient;
