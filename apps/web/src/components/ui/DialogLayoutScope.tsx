import {createContext, useContext, type ReactNode} from 'react';
import './model-dialog-layout.css';

const ModelDialogLayout = createContext(false);

export function ModelDialogLayoutScope({children}: {children: ReactNode}) {
  return <ModelDialogLayout.Provider value>{children}</ModelDialogLayout.Provider>;
}

export const useModelDialogLayout = () => useContext(ModelDialogLayout);
