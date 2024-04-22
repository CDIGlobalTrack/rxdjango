import { useState, useEffect } from 'react';
import StateChannel from './StateChannel';

export const useChannelState = <T>(channel: StateChannel<T>) => {
  const [state, setReactState] = useState<T | undefined>(undefined);
  const [noConnectionSince, setNoConnectionSince] = useState<Date | undefined>(undefined);

  useEffect(() => {
    // FIX: passing twice here
    const unsubscribe = channel.subscribe(setReactState, setNoConnectionSince);

    return () => {
      unsubscribe();
    }
  }, [])

  return { state, no_connection_since: noConnectionSince };
}
