/** Reasons that permanently prevent WebSocket reconnection. */
const preventReconnectionReasons = {
    'authentication-error': 'authentication-error',
    'protocol-error': 'protocol-error',
    'no-subscribers': 'no-subscribers',
    'manual-disconnect': 'manual-disconnect'
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
    url;
    protocols;
    initialReconnectInterval;
    reconnectInterval;
    maxReconnectInterval;
    token;
    ws;
    authStatusReceived;
    timer;
    reason;
    lastUpdate = null;
    authStatus;
    onOpen = () => { };
    onClose = () => { };
    onAuth = () => { };
    onRuntimeStateChange = () => { };
    onInitialAnchors = () => { };
    onInstances = () => { };
    onActionResponse = () => { };
    onAnchorPrepend = () => { };
    onSystem = () => { };
    onConnected = () => { };
    onEmpty = () => { };
    onError = () => { };
    /**
     * @param url - WebSocket URL to connect to
     * @param token - Auth token sent as the first message after connection
     * @param protocols - Optional WebSocket sub-protocols
     * @param initialReconnectInterval - Initial reconnect delay in ms (doubles on each retry)
     * @param maxReconnectInterval - Maximum reconnect delay in ms
     */
    constructor(url, token, protocols = [], initialReconnectInterval = 50, maxReconnectInterval = 5000) {
        this.url = url;
        this.protocols = protocols;
        this.initialReconnectInterval = initialReconnectInterval;
        this.reconnectInterval = initialReconnectInterval;
        this.maxReconnectInterval = maxReconnectInterval;
        this.token = token;
        this.authStatusReceived = false;
    }
    /** Initiate WebSocket connection. Sends auth token on open. */
    connect() {
        if (this.ws)
            return;
        this.timer = undefined;
        this.ws = new WebSocket(this.url, this.protocols);
        this.ws.onopen = () => {
            this.ws.send(JSON.stringify({ token: this.token, lastUpdate: this.lastUpdate }));
            this.reconnectInterval = this.initialReconnectInterval;
            this.onOpen();
        };
        this.ws.onmessage = (event) => {
            // Instance arrays are sent as JSON arrays (no type wrapper)
            if (event.data[0] === '[') {
                const instances = JSON.parse(event.data);
                for (const instance of instances) {
                    if (instance._tstamp && instance._tstamp > (this.lastUpdate || 0)) {
                        this.lastUpdate = instance._tstamp;
                    }
                }
                this.onInstances(instances);
                return;
            }
            const message = JSON.parse(event.data);
            switch (message.type) {
                case 'auth':
                    this.authStatusReceived = true;
                    this.authStatus = message;
                    this.onAuth(this.authStatus);
                    if (this.authStatus.statusCode === 200) {
                        this.onConnected();
                    }
                    else if (this.authStatus.error) {
                        console.error("Authentication Error:", this.authStatus.error);
                        this.onError(new Error(this.authStatus.error));
                        this.disconnect('authentication-error');
                    }
                    break;
                case 'initialAnchors':
                    if (message.anchorIds.length > 0) {
                        this.onInitialAnchors(message.anchorIds);
                    }
                    else {
                        this.onEmpty();
                    }
                    break;
                case 'prependAnchor':
                    this.onAnchorPrepend(message.anchorId);
                    break;
                case 'actionResponse':
                    this.onActionResponse(message);
                    break;
                case 'runtimeVar':
                    this.onRuntimeStateChange(message);
                    break;
                case 'system':
                    this.onSystem(message);
                    break;
                case 'maintenance':
                    this.persistentReconnect();
                    break;
                default:
                    if (typeof process !== 'undefined' && process.env?.NODE_ENV !== 'production') {
                        console.warn('RxDjango: Unknown message type:', message.type, message);
                    }
                    break;
            }
        };
        this.ws.onclose = (event) => {
            if (this.reason && preventReconnectionReasons[this.reason]) {
                this.onClose(event);
                return;
            }
            this.persistentReconnect(event.wasClean);
            this.onClose(event);
        };
    }
    /** Send a string message through the WebSocket. Logs error if not connected. */
    send(data) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(data);
        }
        else {
            console.error("WebSocket is not open. Ready state:", this.ws?.readyState);
        }
    }
    /** Attempt to reconnect with exponential backoff. */
    persistentReconnect(wasClean = false) {
        this.ws = undefined;
        if (!this.authStatusReceived || !wasClean) {
            console.warn("WebSocket disconnected. Reconnecting in", this.reconnectInterval, "ms");
            this.timer = setTimeout(() => this.connect(), this.reconnectInterval);
            // Double the reconnect interval for the next potential reconnection, but cap it at the max value
            this.reconnectInterval = Math.min(this.reconnectInterval * 2, this.maxReconnectInterval);
        }
    }
    /**
     * Close the WebSocket connection.
     *
     * @param reason - If provided, prevents automatic reconnection
     */
    disconnect(reason) {
        if (this.timer && reason) {
            clearTimeout(this.timer);
            this.timer = undefined;
        }
        this.reason = reason;
        if (reason) {
            this.lastUpdate = null;
        }
        this.ws?.close();
    }
}
