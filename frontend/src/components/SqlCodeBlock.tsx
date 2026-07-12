import React from "react";

interface SqlCodeBlockProps {
  sql: string;
}

const SQL_KEYWORDS = new Set([
  "ALL", "AND", "AS", "ASC", "BETWEEN", "BY", "CASE", "DESC", "DISTINCT",
  "ELSE", "END", "FROM", "FULL", "GROUP", "HAVING", "IN", "INNER", "IS",
  "JOIN", "LEFT", "LIMIT", "NOT", "NULL", "OFFSET", "ON", "OR", "ORDER",
  "OUTER", "RIGHT", "SELECT", "THEN", "UNION", "WHEN", "WHERE", "WITH"
]);

const SQL_FUNCTIONS = new Set([
  "AVG", "COUNT", "DATE_TRUNC", "MAX", "MIN", "NULLIF", "SUM", "TOSTARTOFMONTH"
]);

const TOKEN_PATTERN = /(--[^\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|`(?:``|[^`])*`|\b[A-Za-z_][A-Za-z0-9_]*\b|\b\d+(?:\.\d+)?\b)/g;

const getTokenClass = (token: string) => {
  const upperToken = token.toUpperCase();

  if (token.startsWith("--") || token.startsWith("/*")) return "sql-token--comment";
  if (token.startsWith("'")) return "sql-token--string";
  if (token.startsWith('"') || token.startsWith("`")) return "sql-token--identifier";
  if (/^\d+(?:\.\d+)?$/.test(token)) return "sql-token--number";
  if (SQL_FUNCTIONS.has(upperToken)) return "sql-token--function";
  if (SQL_KEYWORDS.has(upperToken)) return "sql-token--keyword";

  return "";
};

export const SqlCodeBlock: React.FC<SqlCodeBlockProps> = ({ sql }) => {
  const tokens = sql.split(TOKEN_PATTERN);

  return (
    <pre className="sql-code-block" aria-label="格式化 SQL">
      <code>
        {tokens.map((token, index) => {
          const tokenClass = getTokenClass(token);
          return tokenClass ? (
            <span key={index} className={tokenClass}>{token}</span>
          ) : (
            <React.Fragment key={index}>{token}</React.Fragment>
          );
        })}
      </code>
    </pre>
  );
};
