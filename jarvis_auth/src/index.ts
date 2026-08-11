export {
  AuthClient,
  AuthError,
  authClient,
  getActiveProfileId,
  getCsrfToken,
  setActiveProfileId,
  setCsrfToken,
} from './client'
export type { AuthClientOptions, AuthStatus, ProfileListResponse, UserProfile } from './client'
export { LockGate } from './LockGate'
export type { LockGateProps } from './LockGate'
export { useLockGate } from './useLockGate'
export type { LockGateState, UseLockGateOptions } from './useLockGate'
