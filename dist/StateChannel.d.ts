import { NoConnectionListener, Listener, Model } from './StateChannel.interfaces';
declare abstract class StateChannel<T> {
    private ws;
    private builder;
    private listeners;
    private noConnectionListeners;
    private token;
    protected args: {
        [key: string]: number | string;
    };
    abstract endpoint: string;
    abstract anchor: string;
    abstract baseURL: string;
    abstract model: Model;
    constructor(token: string);
    init(): void;
    private receiveInstances;
    private notify;
    subscribe(listener: Listener<T>, noConnectionListener?: NoConnectionListener): () => void;
    private getEndpoint;
    private onopen;
    private onclose;
}
export default StateChannel;
