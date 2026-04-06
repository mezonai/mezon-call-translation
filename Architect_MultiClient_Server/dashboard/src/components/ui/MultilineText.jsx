/** Renders text with line breaks from real `\n` or literal `\n` in the string. */
const MultilineText = ({ text, className = '' }) => {
  const normalized = text == null ? '' : String(text).replace(/\\n/g, '\n');
  return (
    <p className={['whitespace-pre-line', className].filter(Boolean).join(' ')}>
      {normalized}
    </p>
  );
};

export default MultilineText;
