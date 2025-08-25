import { useState, useEffect } from 'react';
import ContextChannel from './ContextChannel';
import { InstanceType } from './ContextChannel.interfaces';

export const useChannelState = <T, Y=unknown>(channel: ContextChannel<T> | undefined) => {
  const [state, setReactState] = useState<T>();
  const [runtimeState, setRuntimeState] = useState<Y>();
  const [connected, setConnected] = useState<boolean>(false);
  const [empty, setEmpty] = useState<boolean>(false);
  const [error, setError] = useState<Error>();
  const [noConnectionSince, setNoConnectionSince] = useState<Date>();

  useEffect(() => {
    if (!channel) return;
    
    channel.onConnected = () => {
      setConnected(true);
    };

    channel.onError = (error: Error) => {
      setError(error);
    };

    channel.onEmpty = () => {
      setEmpty(true);
    };

    const unsubscribe = channel.subscribe(setReactState, setNoConnectionSince);
    const runtimeUnsubscribe = channel.runtimeState === null ? null : channel.subscribeRuntimeState((rs) => setRuntimeState(rs as Y));

    return () => {
      unsubscribe();
      if (runtimeUnsubscribe) runtimeUnsubscribe();
    };
  }, [channel]);

  if (!channel) return { state: undefined, connected: false, no_connection_since: undefined, runtimeState: undefined, empty: false, error: undefined };
  return { state, connected, no_connection_since: noConnectionSince, runtimeState, empty, error };
};

export const useChannelInstance = <T, Y>(channel: ContextChannel<T> | undefined, instance_type: string, instance_id: number | undefined) => {
  const [instance, setInstance] = useState<Y>();
  const [connected, setConnected] = useState<boolean>(false);
  
  useEffect(() => {
    if (!channel) return;
    channel.onConnected = () => setConnected(true);
  }, [channel, instance_type, instance_id]);

  useEffect(() => {
    if (!channel || !instance_id) return;
    const handleSetInstance = (i: InstanceType) => setInstance(i as Y);
    const unsubscribe = channel.subscribeInstance(handleSetInstance, instance_id, instance_type);
    return () => {
      unsubscribe();
    }
  }, [connected, instance_id]);

  if (!channel || !instance_id) return null;
  return instance;
};
