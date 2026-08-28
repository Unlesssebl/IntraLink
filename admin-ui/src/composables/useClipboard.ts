import { useToastStore } from '../stores/toast';

export const useClipboard = () => {
  const toast = useToastStore();

  const copyText = async (text: string, label: string = 'Значение'): Promise<boolean> => {
    if (!text) return false;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      toast.success(`${label} скопировано в буфер: ${text}`, 'Скопировано', 2500);
      return true;
    } catch (e) {
      console.error('Failed to copy text:', e);
      toast.error(`Не удалось скопировать ${label.toLowerCase()}`);
      return false;
    }
  };

  return { copyText };
};
