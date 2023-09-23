import { useState, useEffect } from 'react';
import StateChannel from './StateChannel';

export const useChannelState = <T>(channel: StateChannel<T>) => {
  const [state, setReactState] = useState<T | undefined>(undefined);

  useEffect(() => {
    // FIX: passing twice here
    channel.init();
    const unsubscribe = channel.subscribe(setReactState);

    return () => {
      unsubscribe();
    }
  }, [])

  return state;
}
