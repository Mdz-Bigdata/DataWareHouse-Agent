#!/bin/bash
# 免 Python 依赖的 MySQL SQL 导入工具。
# 依靠系统已安装的 mysql 命令行客户端。
# 实现了与 Python 脚本相同的幂等性过滤机制（忽略 1007, 1050, 1060, 1061, 1062, 1091 等错误码）。

cd "$(dirname "$0")/.."

# 默认端口
MYSQL_PORT_INPUT=3306

read -r -p "MySQL host: " MYSQL_HOST_INPUT
read -r -p "MySQL port [3306]: " MYSQL_PORT_INPUT
read -r -p "MySQL user: " MYSQL_USER_INPUT
read -r -s -p "MySQL password: " MYSQL_PASSWORD_INPUT
echo
read -r -p "Target database: " MYSQL_DATABASE_INPUT

MYSQL_PORT_INPUT=${MYSQL_PORT_INPUT:-3306}

if [ -z "$MYSQL_HOST_INPUT" ] || [ -z "$MYSQL_USER_INPUT" ] || [ -z "$MYSQL_DATABASE_INPUT" ]; then
    echo "❌ Host、User、Target database 都必须手动输入。"
    exit 1
fi

# 检查是否有 mysql 客户端
if ! command -v mysql >/dev/null 2>&1; then
    echo "❌ 错误: 未在系统 PATH 中找到 'mysql' 命令行客户端。"
    echo "请先安装 mysql-client，或使用带 Python 虚拟环境的 ./apply-sql.sh 脚本。"
    exit 1
fi

echo "---------------------------------------------------"
echo "请确认本次 SQL 执行目标："
echo "  Host     : $MYSQL_HOST_INPUT"
echo "  Port     : $MYSQL_PORT_INPUT"
echo "  User     : $MYSQL_USER_INPUT"
echo "  Database : $MYSQL_DATABASE_INPUT"
echo "  Password : ******"
if [ $# -eq 0 ]; then
    echo "  SQL files: db-prod/V*.sql"
else
    echo "  SQL file : $*"
fi
read -r -p "确认无误请输入 YES 继续执行：" CONFIRM_INPUT
CONFIRM_UPPER=$(echo "$CONFIRM_INPUT" | tr '[:lower:]' '[:upper:]')
if [ "$CONFIRM_UPPER" != "YES" ]; then
    echo "❌ 已取消，未执行 SQL。"
    exit 1
fi

# 定义需要忽略的 MySQL 错误码
# 1007: 数据库已存在
# 1050: 表已存在
# 1060: 重复的列名
# 1061: 重复的键/索引名
# 1062: 唯一性约束重复键值
# 1091: 试图删除不存在的列或键
IGNORED_ERRORS="1007|1050|1060|1061|1062|1091"

# 检查连接并尝试创建数据库（若不存在）
MYSQL_BASE_CMD="mysql -h $MYSQL_HOST_INPUT -P $MYSQL_PORT_INPUT -u $MYSQL_USER_INPUT -p$MYSQL_PASSWORD_INPUT"
CREATE_DB_SQL="CREATE DATABASE IF NOT EXISTS \`$MYSQL_DATABASE_INPUT\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"

echo "🔌 正在连接 MySQL 并确保目标数据库已存在..."
if ! echo "$CREATE_DB_SQL" | $MYSQL_BASE_CMD >/dev/null 2>&1; then
    echo "❌ 数据库连接或创建失败，请检查连接参数（如 Host、User、Password）或数据库服务状态。"
    exit 1
fi

# 数据库连接参数
MYSQL_CMD="$MYSQL_BASE_CMD $MYSQL_DATABASE_INPUT"

# 单个 SQL 文件执行逻辑（包含切分语句和错误捕获）
execute_sql_file() {
    local sql_file="$1"
    echo "📖 Reading $sql_file..."
    
    # 临时文件用来收集错误输出
    local err_log
    err_log=$(mktemp)
    
    # 拆分逻辑：通过维护字符串开启/闭合状态，避开多行字符串（提示词）内部的分号，安全完成语句切分
    local stmt=""
    local in_string=0
    
    # 用来读取 SQL 文件
    while IFS= read -r line || [[ -n "$line" ]]; do
        # 仅在非多行字符串状态下，才忽略空行和 -- 或 # 注释行
        clean_line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        if [ $in_string -eq 0 ]; then
            if [ -z "$clean_line" ] || [[ "$clean_line" =~ ^-- ]] || [[ "$clean_line" =~ ^/\* ]]; then
                continue
            fi
        fi
        
        if [ -z "$stmt" ]; then
            stmt="$line"
        else
            stmt="${stmt}"$'\n'"$line"
        fi
        
        # 统计当前行中未转义单引号的数量，以精确跟踪跨行字符串开启/闭合状态
        # 1. 移除转义的单引号 \' 和双单引号 ''
        local temp
        temp="${line//\\\'/}"
        temp="${temp//\'\'/}"
        # 2. 去掉所有非单引号字符，统计剩下的单引号数量
        local only_quotes
        only_quotes="${temp//[^\']/}"
        local num_quotes=${#only_quotes}
        
        # 如果单引号数量为奇数，翻转多行字符串状态
        if (( num_quotes % 2 != 0 )); then
            in_string=$((1 - in_string))
        fi
        
        # 当且仅当不在多行字符串内，且行尾为分号时，说明一条完整的 SQL 语句结束了
        if [ $in_string -eq 0 ] && [[ "$clean_line" =~ \;$ ]]; then
            # 过滤 USE 或 CREATE DATABASE 语句
            if [[ "$stmt" =~ ^[[:space:]]*(CREATE[[:space:]]+DATABASE|USE)[[:space:]] ]]; then
                stmt=""
                continue
            fi
            
            # 去除末尾分号（与 Python 版 apply_sql.py 保持一致）
            local exec_stmt="$stmt"
            exec_stmt="${exec_stmt%;}"
            exec_stmt="${exec_stmt%"${exec_stmt##*[![:space:]]}"}"

            # 执行单条语句并捕获报错（保留换行，避免行内 -- 注释吞掉后续 SQL）
            set +e
            printf '%s\n' "$exec_stmt" | $MYSQL_CMD 2>"$err_log"
            status=$?
            set -e
            
            if [ $status -ne 0 ]; then
                err_msg=$(cat "$err_log")
                # 检查错误码是否属于被忽略的错误
                is_ignored=0
                for code in ${IGNORED_ERRORS//|/ }; do
                    if [[ "$err_msg" =~ "ERROR $code" ]] || [[ "$err_msg" =~ "Error $code" ]]; then
                        echo "   -> Skipping (already applied): $(echo "$err_msg" | tr '\n' ' ')"
                        is_ignored=1
                        break
                    fi
                done
                
                if [ $is_ignored -eq 0 ]; then
                    echo "❌ Error executing statement:"
                    echo "Statement: ${stmt:0:150}..."
                    echo "Error message: $err_msg"
                    rm -f "$err_log"
                    return 1
                fi
            fi
            stmt=""
        fi
    done < "$sql_file"
    
    # 扫尾：处理文件末尾可能没加分号的最后一条语句
    if [ -n "$stmt" ]; then
        local clean_stmt
        clean_stmt=$(echo "$stmt" | sed '/^[[:space:]]*--/d; /^[[:space:]]*#/d; s/^[[:space:]]*//; s/[[:space:]]*$//')
        if [ -n "$clean_stmt" ]; then
            if ! [[ "$clean_stmt" =~ ^[[:space:]]*(CREATE[[:space:]]+DATABASE|USE)[[:space:]] ]]; then
                local exec_stmt="$clean_stmt"
                exec_stmt="${exec_stmt%;}"
                exec_stmt="${exec_stmt%"${exec_stmt##*[![:space:]]}"}"
                set +e
                printf '%s\n' "$exec_stmt" | $MYSQL_CMD 2>"$err_log"
                status=$?
                set -e
                if [ $status -ne 0 ]; then
                    err_msg=$(cat "$err_log")
                    is_ignored=0
                    for code in ${IGNORED_ERRORS//|/ }; do
                        if [[ "$err_msg" =~ "ERROR $code" ]] || [[ "$err_msg" =~ "Error $code" ]]; then
                            echo "   -> Skipping (already applied): $err_msg"
                            is_ignored=1
                            break
                        fi
                    done
                    if [ $is_ignored -eq 0 ]; then
                        echo "❌ Error executing statement:"
                        echo "Statement: ${clean_stmt:0:150}..."
                        echo "Error message: $err_msg"
                        rm -f "$err_log"
                        return 1
                    fi
                fi
            fi
        fi
    fi
    
    rm -f "$err_log"
    return 0
}

if [ $# -eq 0 ]; then
    DB_DIR="db-prod"
    if [ ! -d "$DB_DIR" ]; then
        echo "❌ 找不到目录 $DB_DIR"
        exit 1
    fi

    # 按版本自然顺序排序
    FILES=$(ls "$DB_DIR"/V*.sql 2>/dev/null | sort -V)
    if [ -z "$FILES" ]; then
        echo "❌ 未在 $DB_DIR 中找到任何 V*.sql 迁移文件"
        exit 1
    fi

    for f in $FILES; do
        echo "---------------------------------------------------"
        echo "🚀 Applying $f..."
        if ! execute_sql_file "$f"; then
            echo "❌ Failed to apply $f"
            exit 1
        fi
    done
    echo "---------------------------------------------------"
    echo "✅ 所有数据库结构初始化迁移 SQL 文件执行成功。"
    
    # 交互询问是否导入管理员账号
    read -r -p "是否需要顺带导入默认管理员账号和预置 API Key 凭证？ (推荐首次部署时导入) [Y/N]: " RUN_INIT_ADMIN
    RUN_INIT_ADMIN_UPPER=$(echo "$RUN_INIT_ADMIN" | tr '[:lower:]' '[:upper:]')
    if [ "$RUN_INIT_ADMIN_UPPER" == "Y" ] || [ "$RUN_INIT_ADMIN_UPPER" == "YES" ]; then
        ADMIN_SQL="db-prod/INIT-USER-ADMIN.sql"
        if [ -f "$ADMIN_SQL" ]; then
            echo "---------------------------------------------------"
            echo "🚀 正在导入默认管理员账号数据 ($ADMIN_SQL)..."
            if ! execute_sql_file "$ADMIN_SQL"; then
                echo "❌ 默认管理员账号数据导入失败。"
                exit 1
            fi
            echo "---------------------------------------------------"
            echo "✅ 默认管理员账号数据导入成功！"
            echo -e "\033[1;32m===================================================\033[0m"
            echo -e "\033[1;32m🔑 首次登录重要指引：\033[0m"
            echo -e "  - \033[1;36m默认用户名\033[0m  : admin"
            echo -e "  - \033[1;36m预置 API Key\033[0m: 5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c"
            echo -e "  - \033[1;33m登录方式\033[0m    : 在系统登录框中复制并粘贴上述 API Key 即可登录。"
            echo -e "  - \033[1;31m安全提醒\033[0m    : 首次登录成功后，请务必前往【用户管理】"
            echo -e "                或【个人中心】为 admin 设置密码，以启用常规密码登录。"
            echo -e "\033[1;32m===================================================\033[0m"
        else
            echo "⚠️ 未找到默认管理员数据文件 $ADMIN_SQL，跳过导入。"
        fi
    else
        echo "💡 已跳过默认管理员账号数据的导入。"
    fi
else
    for f in "$@"; do
        echo "---------------------------------------------------"
        echo "🚀 Applying $f..."
        if ! execute_sql_file "$f"; then
            echo "❌ Failed to apply $f"
            exit 1
        fi
        if [[ "$f" =~ INIT-USER-ADMIN.sql$ ]]; then
            echo -e "\033[1;32m===================================================\033[0m"
            echo -e "\033[1;32m🔑 首次登录重要指引：\033[0m"
            echo -e "  - \033[1;36m默认用户名\033[0m  : admin"
            echo -e "  - \033[1;36m预置 API Key\033[0m: 5BYfsKWhU_Cfx83cuo8E0kd4AtEhlUHDVlKwwR2kN-c"
            echo -e "  - \033[1;33m登录方式\033[0m    : 在系统登录框中复制并粘贴上述 API Key 即可登录。"
            echo -e "  - \033[1;31m安全提醒\033[0m    : 首次登录成功后，请务必前往【用户管理】"
            echo -e "                或【个人中心】为 admin 设置密码，以启用常规密码登录。"
            echo -e "\033[1;32m===================================================\033[0m"
        fi
    done
fi
