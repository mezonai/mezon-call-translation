import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import {
  getCurrentUser,
  logoutSession,
  refreshAuthTokens,
  setAuthRefreshHandler
} from '../services/api';

const AuthContext = createContext(null);
let refreshPromise = null;

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
          const { accessToken: storedToken, refreshToken: storedRefreshToken, user: storedUser } = JSON.parse(authData);

          if (storedToken && storedRefreshToken) {
            setAccessToken(storedToken);
            setRefreshToken(storedRefreshToken);
            if (storedUser) {
              setUser(storedUser);
            }

            // Verify token and fetch user info
            try {
              const data = await getCurrentUser(storedToken);

              if (data && data.user) {
                setUser(data.user);
              } else {
                // Invalid token, clear storage
                localStorage.removeItem('auth');
                setAccessToken(null);
                setRefreshToken(null);
                setUser(null);
              }
            } catch (error) {
              console.error('Failed to verify token:', error);

              // If 401, try to refresh token
              if (error.response?.status === 401 && storedRefreshToken) {
                const refreshed = await refreshAccessToken(storedRefreshToken);
                if (!refreshed?.accessToken) {
                  // Refresh failed, clear everything
                  localStorage.removeItem('auth');
                  setAccessToken(null);
                  setRefreshToken(null);
                  setUser(null);
                }
              } else {
                // Other error, clear storage
                localStorage.removeItem('auth');
                setAccessToken(null);
                setRefreshToken(null);
                setUser(null);
              }
            }
          }
        } catch (err) {
          console.error('Failed to parse auth data:', err);
          localStorage.removeItem('auth');
          setUser(null);
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
      refreshToken: newRefreshToken,
      user: userData
    }));
  };

  const refreshAccessToken = useCallback(async (refreshTokenOverride = null) => {
    if (!refreshPromise) {
      refreshPromise = (async () => {
        const tokenToUse = refreshTokenOverride || refreshToken;
        if (!tokenToUse) {
          throw new Error('Missing refresh token');
        }

        const data = await refreshAuthTokens(tokenToUse);

        if (!data || !data.access_token || !data.refresh_token) {
          throw new Error('No access token or refresh token in refresh response');
        }

        const newAccessToken = data.access_token;
        const newRefreshToken = data.refresh_token;
        setAccessToken(newAccessToken);
        setRefreshToken(newRefreshToken);


        const authData = localStorage.getItem('auth');
        let storedUser = null;
        if (authData) {
          try {
            storedUser = JSON.parse(authData).user || null;
          } catch (err) {
            console.error('Failed to parse auth data:', err);
          }
        }

        localStorage.setItem('auth', JSON.stringify({
          accessToken: newAccessToken,
          refreshToken: newRefreshToken,
          user: storedUser || user
        }));

        console.log('Access token refreshed successfully');
        return {
          accessToken: newAccessToken,
          refreshToken: newRefreshToken
        };
      })();
    }

    const currentPromise = refreshPromise;
    try {
      return await currentPromise;
    } catch (error) {
      console.error('Failed to refresh token:', error);
      return null;
    } finally {
      if (refreshPromise === currentPromise) {
        refreshPromise = null;
      }
    }
  }, [refreshToken, user]);

  useEffect(() => {
    setAuthRefreshHandler(refreshAccessToken);
    return () => setAuthRefreshHandler(null);
  }, [refreshAccessToken]);

  const logout = async () => {
    try {
      // Call backend to revoke tokens
      if (accessToken && refreshToken) {
        await logoutSession(accessToken, refreshToken);
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

  const value = {
    user,
    accessToken,
    refreshToken,
    loading,
    login,
    logout,
    refreshAccessToken,
    isAuthenticated
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};
