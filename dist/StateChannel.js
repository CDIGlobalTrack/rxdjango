import PersistentWebsocket from './PersistentWebsocket';
import StateBuilder from './StateBuilder';
class StateChannel {
    ws;
    builder;
    listeners = [];
    noConnectionListeners = [];
    token;
    args = {};
    constructor(token) {
        this.token = token;
    }
    init() {
        if (this.builder)
            return;
        this.builder = new StateBuilder(this.model, this.anchor);
        const ws = new PersistentWebsocket(this.getEndpoint(), this.token);
        ws.onclose = this.onclose.bind(this);
        ws.onopen = this.onopen.bind(this);
        ws.oninstances = (instances) => {
            this.receiveInstances(instances);
        };
        this.ws = ws;
    }
    receiveInstances(instances) {
        this.builder.update(instances);
        this.notify();
    }
    notify() {
        for (const listener of this.listeners) {
            if (this.builder.state)
                listener(this.builder.state);
        }
    }
    subscribe(listener, noConnectionListener) {
        if (!this.ws)
            this.init();
        this.listeners.push(listener);
        if (noConnectionListener) {
            this.noConnectionListeners.push(noConnectionListener);
        }
        if (this.listeners.length === 1)
            this.ws.connect();
        const unsubscribe = () => {
            const index = this.listeners.indexOf(listener);
            if (index !== -1) {
                this.listeners.splice(index, 1);
                if (this.listeners.length === 0)
                    this.ws.disconnect();
            }
        };
        return unsubscribe;
    }
    getEndpoint() {
        let constructedEndpoint = this.endpoint;
        // Use a regex to find all the placeholders
        const matches = this.endpoint.match(/{\w+}/g);
        if (matches) {
            matches.forEach((match) => {
                // Extract the property name from the placeholder
                const propertyName = match.replace(/[{}]/g, "");
                const propertyValue = this.args[propertyName];
                // Substitute the placeholder with the property value, if it exists
                constructedEndpoint = constructedEndpoint.replace(match, propertyValue.toString());
            });
        }
        return `${this.baseURL}${constructedEndpoint}`;
    }
    onopen() {
        for (const listener of this.noConnectionListeners) {
            listener(undefined);
        }
    }
    onclose() {
        const now = new Date();
        for (const listener of this.noConnectionListeners) {
            listener(now);
        }
    }
}
export default StateChannel;
