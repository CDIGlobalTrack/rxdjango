import { AuthStatus, TempInstance, SystemMessage } from './ContextChannel.interfaces';
import { ActionResponse } from './actions.d';
/** Reasons that permanently prevent WebSocket reconnection. */
declare const preventReconnectionReasons: {
    'authentication-error': string;
    'protocol-error': string;
    'no-subscribers': string;
    'manual-disconnect': string;
};
/**
 * WebSocket wrapper with automatic reconnection and message routing.
 *
 * Handles connection drops gracefully with exponential backoff retry.
 * Routes incoming messages to appropriate handlers based on message type
 * (instances, action responses, runtime state changes, etc.).
 *
 * @example
 * ```typescript
 * const ws = new PersistentWebSocket('wss://example.com/ws/', authToken);
 * ws.onInstances = (instances) => { ... };
 * ws.onConnected = () => { ... };
 * ws.connect();
 * ```
 */
export default class PersistentWebSocket {
    private url;
    private protocols;
    private initialReconnectInterval;
    private reconnectInterval;
    private maxReconnectInterval;
    private token;
    private ws;
    private authStatusReceived;
    private timer;
    private reason;
    authStatus: AuthStatus | undefined;
    onOpen: () => void;
    onClose: (event: CloseEvent) => void;
    onAuth: (authStatus: AuthStatus) => void;
    onRuntimeStateChange: (runtimeState: unknown) => void;
    onInitialAnchors: (anchors: number[]) => void;
    onInstances: (instances: TempInstance[]) => void;
    onActionResponse: (response: ActionResponse<unknown>) => void;
    onAnchorPrepend: (anchorId: number) => void;
    onSystem: (message: SystemMessage) => void;
    onConnected: () => void;
    onEmpty: () => void;
    onError: (error: Error) => void;
    /**
     * @param url - WebSocket URL to connect to
     * @param token - Auth token sent as the first message after connection
     * @param protocols - Optional WebSocket sub-protocols
     * @param initialReconnectInterval - Initial reconnect delay in ms (doubles on each retry)
     * @param maxReconnectInterval - Maximum reconnect delay in ms
     */
    constructor(url: string, token: string, protocols?: never[], initialReconnectInterval?: number, maxReconnectInterval?: number);
    /** Initiate WebSocket connection. Sends auth token on open. */
    connect(): void;
    /** Send a string message through the WebSocket. Logs error if not connected. */
    send(data: string): void;
    /** Attempt to reconnect with exponential backoff. */
    persistentReconnect(wasClean?: boolean): void;
    /**
     * Close the WebSocket connection.
     *
     * @param reason - If provided, prevents automatic reconnection
     */
    disconnect(reason?: keyof typeof preventReconnectionReasons): void;
}
export {};
