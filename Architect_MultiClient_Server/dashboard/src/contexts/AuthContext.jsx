import { createContext, useContext, useState, useEffect } from 'react';
import apiClient from '../services/api';

const AuthContext = createContext(null);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [accessToken, setAccessToken] = useState(null);
  const [refreshToken, setRefreshToken] = useState(null);
  const [loading, setLoading] = useState(true);

  // Initialize auth state from localStorage on mount
  useEffect(() => {
    const initAuth = async () => {
      const authData = localStorage.getItem('auth');

      if (authData) {
        try {
          const { accessToken: storedToken, refreshToken: storedRefreshToken } = JSON.parse(authData);

          if (storedToken && storedRefreshToken) {
            setAccessToken(storedToken);
            setRefreshToken(storedRefreshToken);

            // Verify token and fetch user info
            try {
              const response = await apiClient.get('/api/auth/mezon/userinfo', {
                headers: {
                  'Authorization': `Bearer ${storedToken}`
                }
              });

              if (response.data && response.data.user) {
                setUser(response.data.user);
              } else {
                // Invalid token, clear storage
                localStorage.removeItem('auth');
                setAccessToken(null);
                setRefreshToken(null);
              }
            } catch (error) {
              console.error('Failed to verify token:', error);

              // If 401, try to refresh token
              if (error.response?.status === 401 && storedRefreshToken) {
                const refreshed = await refreshAccessToken(storedRefreshToken);
                if (!refreshed) {
                  // Refresh failed, clear everything
                  localStorage.removeItem('auth');
                  setAccessToken(null);
                  setRefreshToken(null);
                }
              } else {
                // Other error, clear storage
                localStorage.removeItem('auth');
                setAccessToken(null);
                setRefreshToken(null);
              }
            }
          }
        } catch (err) {
          console.error('Failed to parse auth data:', err);
          localStorage.removeItem('auth');
        }
      }

      setLoading(false);
    };

    initAuth();
  }, []);

  const login = (accessToken, newRefreshToken, userData) => {
    setAccessToken(accessToken);
    setRefreshToken(newRefreshToken);
    setUser(userData);
    localStorage.setItem('auth', JSON.stringify({
      accessToken: accessToken,
      refreshToken: newRefreshToken
    }));
  };

  const refreshAccessToken = async (currentRefreshToken) => {
    try {
      const response = await apiClient.post('/api/auth/mezon/refresh', {
        refresh_token: currentRefreshToken || refreshToken
      });

      if (response.data && response.data.access_token) {
        const newAccessToken = response.data.access_token;
        setAccessToken(newAccessToken);

        // Update stored token
        const authData = localStorage.getItem('auth');
        if (authData) {
          const parsed = JSON.parse(authData);
          localStorage.setItem('auth', JSON.stringify({
            accessToken: newAccessToken,
            refreshToken: parsed.refreshToken
          }));
        }

        console.log('Access token refreshed successfully');
        return true;
      }
      return false;
    } catch (error) {
      console.error('Failed to refresh token:', error);
      return false;
    }
  };

  const logout = async () => {
    try {
      // Call backend to revoke tokens
      if (accessToken && refreshToken) {
        await apiClient.post('/api/auth/mezon/logout',
          { refresh_token: refreshToken },
          { headers: { 'Authorization': `Bearer ${accessToken}` } }
        );
      }
    } catch (error) {
      console.error('Logout API call failed:', error);
      // Continue with local cleanup even if API call fails
    } finally {
      // Clear local state and storage
      setAccessToken(null);
      setRefreshToken(null);
      setUser(null);
      localStorage.removeItem('auth');
    }
  };

  const isAuthenticated = () => {
    return !!accessToken && !!user;
  };

  const getAuthHeader = () => {
    if (accessToken) {
      return { 'Authorization': `Bearer ${accessToken}` };
    }
    return {};
  };

  const value = {
    user,
    accessToken,
    refreshToken,
    loading,
    login,
    logout,
    refreshAccessToken,
    isAuthenticated,
    getAuthHeader
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};
