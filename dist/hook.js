import { useState, useEffect } from 'react';
/**
 * React hook for subscribing to ContextChannel state.
 *
 * Manages the full connection lifecycle: subscribes on mount, unsubscribes on
 * unmount, and tracks connection status, errors, and empty states.
 *
 * @template T - The type of the channel state
 * @template Y - The type of the runtime state (optional)
 * @param channel - The ContextChannel instance to subscribe to, or undefined
 * @returns Object with `state`, `connected`, `no_connection_since`, `runtimeState`, `empty`, and `error`
 *
 * @example
 * ```tsx
 * function ProjectView({ projectId }: { projectId: number }) {
 *   const channel = useMemo(() => new ProjectChannel(authToken), []);
 *   const { state: project, connected, error } = useChannelState(channel);
 *
 *   if (!connected) return <LoadingSpinner />;
 *   if (error) return <ErrorMessage error={error} />;
 *   return <h1>{project.name}</h1>;
 * }
 * ```
 */
export const useChannelState = (channel) => {
    const [state, setReactState] = useState();
    const [runtimeState, setRuntimeState] = useState();
    const [connected, setConnected] = useState(false);
    const [empty, setEmpty] = useState(false);
    const [error, setError] = useState();
    const [noConnectionSince, setNoConnectionSince] = useState();
    useEffect(() => {
        if (!channel)
            return;
        channel.onConnected = () => {
            setConnected(true);
        };
        channel.onError = (error) => {
            setError(error);
        };
        channel.onEmpty = () => {
            setEmpty(true);
        };
        const unsubscribe = channel.subscribe(setReactState, setNoConnectionSince);
        const runtimeUnsubscribe = channel.runtimeState === null ? null : channel.subscribeRuntimeState((rs) => setRuntimeState(rs));
        return () => {
            unsubscribe();
            if (runtimeUnsubscribe)
                runtimeUnsubscribe();
        };
    }, [channel]);
    if (!channel)
        return { state: undefined, connected: false, no_connection_since: undefined, runtimeState: undefined, empty: false, error: undefined };
    return { state, connected, no_connection_since: noConnectionSince, runtimeState, empty, error };
};
/**
 * React hook for subscribing to a specific instance within a channel's state.
 *
 * @template T - The type of the channel state
 * @template Y - The type of the instance
 * @param channel - The ContextChannel instance
 * @param instance_type - The `_instance_type` string of the instance to watch
 * @param instance_id - The ID of the instance, or undefined
 * @returns The instance object, or null if not available
 */
export const useChannelInstance = (channel, instance_type, instance_id) => {
    const [instance, setInstance] = useState();
    const [connected, setConnected] = useState(false);
    useEffect(() => {
        if (!channel)
            return;
        channel.onConnected = () => setConnected(true);
    }, [channel, instance_type, instance_id]);
    useEffect(() => {
        if (!channel || !instance_id)
            return;
        const handleSetInstance = (i) => setInstance(i);
        const unsubscribe = channel.subscribeInstance(handleSetInstance, instance_id, instance_type);
        return () => {
            unsubscribe();
        };
    }, [connected, instance_id]);
    if (!channel || !instance_id)
        return null;
    return instance;
};
