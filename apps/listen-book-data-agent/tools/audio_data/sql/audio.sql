SET NAMES utf8mb4;

CREATE TABLE dim_audio_category (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    parent_id BIGINT NULL,
    category_code VARCHAR(64) NOT NULL,
    category_name VARCHAR(128) NOT NULL,
    category_level TINYINT NOT NULL,
    category_type VARCHAR(32) NOT NULL COMMENT '枚举：audiobook,program,podcast,radio,course',
    sort_no INT NOT NULL DEFAULT 0,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_dim_audio_category_code (category_code),
    CONSTRAINT fk_dim_audio_category_parent FOREIGN KEY (
        parent_id
    ) REFERENCES dim_audio_category (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '音频内容分类维表';

CREATE TABLE dim_content_tag (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    parent_id BIGINT NULL,
    tag_code VARCHAR(64) NOT NULL,
    tag_name VARCHAR(128) NOT NULL,
    tag_type VARCHAR(32) NOT NULL COMMENT '枚举：genre,style,scene,audience,topic',
    sort_no INT NOT NULL DEFAULT 0,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_dim_content_tag_code (tag_code),
    CONSTRAINT fk_dim_content_tag_parent FOREIGN KEY (
        parent_id
    ) REFERENCES dim_content_tag (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容标签维表';

CREATE TABLE dim_channel (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    channel_code VARCHAR(64) NOT NULL,
    channel_name VARCHAR(64) NOT NULL,
    channel_type VARCHAR(32) NOT NULL COMMENT '枚举：app,web,mini_program,vehicle,partner',
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_dim_channel_code (channel_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '渠道维表';

CREATE TABLE dim_language (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    language_code VARCHAR(32) NOT NULL,
    language_name VARCHAR(64) NOT NULL,
    sort_no INT NOT NULL DEFAULT 0,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_dim_language_code (language_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '语言维表';

CREATE TABLE dim_currency (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    currency_code VARCHAR(10) NOT NULL,
    currency_name VARCHAR(64) NOT NULL,
    symbol VARCHAR(16) NOT NULL,
    precision_scale TINYINT NOT NULL DEFAULT 2,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_dim_currency_code (currency_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '币种维表';

CREATE TABLE user_account (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_no VARCHAR(64) NOT NULL,
    nickname VARCHAR(128) NOT NULL,
    avatar_url VARCHAR(500) NULL,
    mobile VARCHAR(32) NULL,
    email VARCHAR(128) NULL,
    register_channel_id BIGINT NOT NULL,
    account_status VARCHAR(32) NOT NULL DEFAULT 'normal' COMMENT '枚举：normal,muted,disabled,cancelled',
    last_login_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_account_no (user_no),
    UNIQUE KEY uk_user_account_mobile (mobile),
    UNIQUE KEY uk_user_account_email (email),
    CONSTRAINT fk_user_account_register_channel FOREIGN KEY (
        register_channel_id
    ) REFERENCES dim_channel (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户账号主表';

CREATE TABLE user_profile (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    gender VARCHAR(16) NOT NULL DEFAULT 'unknown' COMMENT '枚举：male,female,unknown',
    birthday DATE NULL,
    province VARCHAR(64) NULL,
    city VARCHAR(64) NULL,
    occupation VARCHAR(64) NULL,
    listening_scene_payload JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_profile_user (user_id),
    CONSTRAINT fk_user_profile_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户画像表';

CREATE TABLE member_account (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    member_level VARCHAR(16) NOT NULL DEFAULT 'normal' COMMENT '枚举：normal,vip,svip',
    member_status VARCHAR(16) NOT NULL DEFAULT 'inactive' COMMENT '枚举：inactive,active,expired,frozen',
    valid_from DATETIME NULL,
    valid_to DATETIME NULL,
    points_balance INT NOT NULL DEFAULT 0,
    growth_value INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_member_account_user (user_id),
    CONSTRAINT fk_member_account_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '会员账户表';

CREATE TABLE user_follow (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    target_type VARCHAR(32) NOT NULL COMMENT '枚举：narrator,author,organization',
    target_id BIGINT NOT NULL,
    follow_status VARCHAR(16) NOT NULL DEFAULT 'following' COMMENT '枚举：following,cancelled',
    followed_at DATETIME NOT NULL,
    cancelled_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_follow_target (user_id, target_type, target_id),
    KEY idx_user_follow_target (target_type, target_id),
    CONSTRAINT fk_user_follow_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '关注关系表';

CREATE TABLE user_preference (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    category_id BIGINT NULL,
    tag_id BIGINT NULL,
    preference_type VARCHAR(32) NOT NULL COMMENT '枚举：category,tag,play_setting',
    preference_payload JSON NULL,
    weight_score DECIMAL(10, 2) NOT NULL DEFAULT 0,
    preference_target_key VARCHAR(128) GENERATED ALWAYS AS (
        CASE
            WHEN preference_type = 'category' THEN CONCAT('category:', category_id)
            WHEN preference_type = 'tag' THEN CONCAT('tag:', tag_id)
            WHEN preference_type = 'play_setting' THEN 'play_setting'
            ELSE CONCAT(
                preference_type, ':', IFNULL(category_id, 0), ':', IFNULL(tag_id, 0)
            )
        END
    ) STORED,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_preference_key (
        user_id, preference_type, preference_target_key
    ),
    CONSTRAINT ck_user_preference_target CHECK (
        (
            preference_type = 'category'
            AND category_id IS NOT NULL
            AND tag_id IS NULL
        )
        OR (
            preference_type = 'tag'
            AND category_id IS NULL
            AND tag_id IS NOT NULL
        )
        OR (
            preference_type = 'play_setting'
            AND category_id IS NULL
            AND tag_id IS NULL
        )
    ),
    CONSTRAINT fk_user_preference_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_user_preference_category FOREIGN KEY (
        category_id
    ) REFERENCES dim_audio_category (id),
    CONSTRAINT fk_user_preference_tag FOREIGN KEY (
        tag_id
    ) REFERENCES dim_content_tag (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户偏好表';

CREATE TABLE user_message (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    message_no VARCHAR(64) NOT NULL,
    sender_user_id BIGINT NULL,
    receiver_user_id BIGINT NOT NULL,
    message_type VARCHAR(32) NOT NULL COMMENT '枚举：private,system,audit,trade,activity',
    target_type VARCHAR(32) NOT NULL DEFAULT 'none' COMMENT
    '枚举：none,album,track,comment,content_order,recharge_order,upload_task,support_ticket',
    target_id BIGINT NULL,
    message_title VARCHAR(255) NULL,
    message_content TEXT NULL,
    read_status VARCHAR(16) NOT NULL DEFAULT 'unread' COMMENT '枚举：unread,read,deleted',
    sent_at DATETIME NOT NULL,
    read_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_message_no (message_no),
    KEY idx_user_message_receiver (receiver_user_id, read_status, sent_at),
    CONSTRAINT fk_user_message_sender FOREIGN KEY (
        sender_user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_user_message_receiver FOREIGN KEY (
        receiver_user_id
    ) REFERENCES user_account (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户消息表';

CREATE TABLE content_organization (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    organization_code VARCHAR(64) NOT NULL,
    organization_name VARCHAR(128) NOT NULL,
    organization_type VARCHAR(32) NOT NULL COMMENT
    '枚举：copyright_owner,publisher,production,mcn,platform',
    contact_name VARCHAR(64) NULL,
    contact_info VARCHAR(255) NULL,
    intro TEXT NULL,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_organization_code (organization_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容机构表';

CREATE TABLE content_author (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    author_code VARCHAR(64) NOT NULL,
    author_name VARCHAR(128) NOT NULL,
    author_type VARCHAR(32) NOT NULL COMMENT '枚举：original,screenwriter,columnist,anonymous',
    intro TEXT NULL,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_author_code (author_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '作者表';

CREATE TABLE content_narrator (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    narrator_code VARCHAR(64) NOT NULL,
    narrator_name VARCHAR(128) NOT NULL,
    avatar_url VARCHAR(500) NULL,
    organization_id BIGINT NULL,
    contract_type VARCHAR(32) NOT NULL COMMENT '枚举：exclusive,signed,open,official',
    intro TEXT NULL,
    follower_count BIGINT NOT NULL DEFAULT 0,
    album_count INT NOT NULL DEFAULT 0,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_narrator_code (narrator_code),
    CONSTRAINT fk_content_narrator_organization FOREIGN KEY (
        organization_id
    ) REFERENCES content_organization (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '主播表';

CREATE TABLE creator_profile (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    creator_no VARCHAR(64) NOT NULL,
    creator_name VARCHAR(128) NOT NULL,
    creator_type VARCHAR(32) NOT NULL COMMENT '枚举：individual,studio,organization,official',
    narrator_id BIGINT NULL,
    organization_id BIGINT NULL,
    certification_status VARCHAR(32) NOT NULL DEFAULT 'uncertified' COMMENT
    '枚举：uncertified,pending,certified,rejected,revoked',
    creator_intro TEXT NULL,
    homepage_url VARCHAR(500) NULL,
    settled_at DATETIME NULL,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_creator_profile_no (creator_no),
    UNIQUE KEY uk_creator_profile_user (user_id),
    CONSTRAINT fk_creator_profile_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_creator_profile_narrator FOREIGN KEY (
        narrator_id
    ) REFERENCES content_narrator (id),
    CONSTRAINT fk_creator_profile_organization FOREIGN KEY (
        organization_id
    ) REFERENCES content_organization (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '创作者档案表';

CREATE TABLE creator_apply_record (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    apply_no VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    creator_id BIGINT NULL,
    organization_id BIGINT NULL,
    apply_type VARCHAR(32) NOT NULL COMMENT
    '枚举：creator_settle,narrator_certification,organization_certification,contract_upgrade',
    apply_payload JSON NULL,
    apply_status VARCHAR(32) NOT NULL DEFAULT 'submitted' COMMENT
    '枚举：submitted,reviewing,approved,rejected,cancelled',
    reject_reason VARCHAR(500) NULL,
    submitted_at DATETIME NOT NULL,
    reviewed_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_creator_apply_record_no (apply_no),
    CONSTRAINT fk_creator_apply_record_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_creator_apply_record_creator FOREIGN KEY (
        creator_id
    ) REFERENCES creator_profile (id),
    CONSTRAINT fk_creator_apply_record_organization FOREIGN KEY (
        organization_id
    ) REFERENCES content_organization (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '创作者入驻申请表';

CREATE TABLE audio_album (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    album_code VARCHAR(64) NOT NULL,
    album_title VARCHAR(255) NOT NULL,
    album_type VARCHAR(32) NOT NULL COMMENT '枚举：audiobook,program,podcast,radio,course',
    category_id BIGINT NOT NULL,
    language_id BIGINT NOT NULL,
    organization_id BIGINT NULL,
    cover_url VARCHAR(500) NULL,
    summary TEXT NULL,
    album_status VARCHAR(32) NOT NULL DEFAULT 'draft' COMMENT
    '枚举：draft,reviewing,published,paused,offline',
    publish_status VARCHAR(32) NOT NULL DEFAULT 'unknown' COMMENT
    '枚举：serializing,completed,unknown',
    age_rating VARCHAR(16) NOT NULL DEFAULT 'all' COMMENT '枚举：all,children,teen,adult',
    track_count INT NOT NULL DEFAULT 0,
    total_duration_seconds BIGINT NOT NULL DEFAULT 0,
    play_count BIGINT NOT NULL DEFAULT 0,
    favorite_count BIGINT NOT NULL DEFAULT 0,
    rating_score DECIMAL(4, 2) NOT NULL DEFAULT 0,
    published_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_audio_album_code (album_code),
    CONSTRAINT fk_audio_album_category FOREIGN KEY (
        category_id
    ) REFERENCES dim_audio_category (id),
    CONSTRAINT fk_audio_album_language FOREIGN KEY (
        language_id
    ) REFERENCES dim_language (id),
    CONSTRAINT fk_audio_album_organization FOREIGN KEY (
        organization_id
    ) REFERENCES content_organization (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '音频专辑表';

CREATE TABLE album_organization_rel (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    album_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    organization_role VARCHAR(32) NOT NULL COMMENT
    '枚举：copyright_owner,publisher,producer,production,distributor,source_platform,mcn',
    authorization_status VARCHAR(32) NOT NULL DEFAULT 'valid' COMMENT
    '枚举：valid,pending,expired,terminated',
    effective_from DATETIME NOT NULL,
    effective_to DATETIME NULL,
    sort_no INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_album_organization_rel (
        album_id, organization_id, organization_role
    ),
    CONSTRAINT fk_album_organization_rel_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_album_organization_rel_organization FOREIGN KEY (
        organization_id
    ) REFERENCES content_organization (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '专辑机构关系表';

CREATE TABLE album_author_rel (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    album_id BIGINT NOT NULL,
    author_id BIGINT NOT NULL,
    author_role VARCHAR(32) NOT NULL COMMENT
    '枚举：original_author,screenwriter,translator,editor',
    sort_no INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_album_author_rel (album_id, author_id, author_role),
    CONSTRAINT fk_album_author_rel_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_album_author_rel_author FOREIGN KEY (
        author_id
    ) REFERENCES content_author (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '专辑作者关系表';

CREATE TABLE album_narrator_rel (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    album_id BIGINT NOT NULL,
    narrator_id BIGINT NOT NULL,
    narrator_role VARCHAR(32) NOT NULL COMMENT '枚举：main,cast,host,guest',
    sort_no INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_album_narrator_rel (album_id, narrator_id, narrator_role),
    CONSTRAINT fk_album_narrator_rel_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_album_narrator_rel_narrator FOREIGN KEY (
        narrator_id
    ) REFERENCES content_narrator (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '专辑主播关系表';

CREATE TABLE album_tag_rel (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    album_id BIGINT NOT NULL,
    tag_id BIGINT NOT NULL,
    sort_no INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_album_tag_rel (album_id, tag_id),
    CONSTRAINT fk_album_tag_rel_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_album_tag_rel_tag FOREIGN KEY (
        tag_id
    ) REFERENCES dim_content_tag (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '专辑标签关系表';

CREATE TABLE audio_track (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    album_id BIGINT NOT NULL,
    track_no INT NOT NULL,
    track_title VARCHAR(255) NOT NULL,
    track_type VARCHAR(32) NOT NULL DEFAULT 'normal' COMMENT
    '枚举：normal,trailer,bonus,live_record',
    duration_seconds INT NOT NULL DEFAULT 0,
    free_flag TINYINT NOT NULL DEFAULT 0,
    trial_seconds INT NOT NULL DEFAULT 0,
    track_status VARCHAR(32) NOT NULL DEFAULT 'draft' COMMENT
    '枚举：draft,reviewing,published,offline',
    play_count BIGINT NOT NULL DEFAULT 0,
    published_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_audio_track_no (album_id, track_no),
    CONSTRAINT fk_audio_track_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '音频章节表';

CREATE TABLE user_bookshelf (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    album_id BIGINT NOT NULL,
    shelf_status VARCHAR(32) NOT NULL COMMENT '枚举：favorited,subscribed,finished,removed',
    last_track_id BIGINT NULL,
    last_position_seconds INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_bookshelf_album (user_id, album_id),
    CONSTRAINT fk_user_bookshelf_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_user_bookshelf_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_user_bookshelf_last_track FOREIGN KEY (
        last_track_id
    ) REFERENCES audio_track (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户书架表';

CREATE TABLE track_audio_file (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    track_id BIGINT NOT NULL,
    file_code VARCHAR(64) NOT NULL,
    file_url VARCHAR(500) NOT NULL,
    file_format VARCHAR(16) NOT NULL COMMENT '枚举：mp3,m4a,aac',
    bitrate_kbps INT NOT NULL,
    sample_rate_hz INT NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    duration_seconds INT NOT NULL DEFAULT 0,
    version_no INT NOT NULL DEFAULT 1,
    is_current TINYINT NOT NULL DEFAULT 1,
    file_status VARCHAR(32) NOT NULL DEFAULT 'processing' COMMENT
    '枚举：available,processing,failed,deleted',
    current_quality_key VARCHAR(160) GENERATED ALWAYS AS (
        CASE
            WHEN is_current = 1 THEN CONCAT(
                track_id, ':', file_format, ':', bitrate_kbps
            )
            ELSE NULL
        END
    ) STORED,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_track_audio_file_code (file_code),
    UNIQUE KEY uk_track_audio_file_version (
        track_id, file_format, bitrate_kbps, version_no
    ),
    UNIQUE KEY uk_track_audio_file_current_quality (current_quality_key),
    CONSTRAINT ck_track_audio_file_current CHECK (is_current IN (0, 1)),
    CONSTRAINT fk_track_audio_file_track FOREIGN KEY (
        track_id
    ) REFERENCES audio_track (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '章节音频文件表';

CREATE TABLE content_upload_task (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    upload_no VARCHAR(64) NOT NULL,
    creator_id BIGINT NOT NULL,
    album_id BIGINT NULL,
    track_id BIGINT NULL,
    file_id BIGINT NULL,
    upload_type VARCHAR(32) NOT NULL COMMENT
    '枚举：album,track,audio_file,cover,batch_tracks',
    source_file_name VARCHAR(255) NULL,
    source_file_url VARCHAR(500) NULL,
    file_size_bytes BIGINT NULL,
    process_status VARCHAR(32) NOT NULL DEFAULT 'submitted' COMMENT
    '枚举：submitted,uploading,uploaded,processing,processed,failed,cancelled',
    failure_reason VARCHAR(500) NULL,
    submitted_at DATETIME NOT NULL,
    processed_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_upload_task_no (upload_no),
    CONSTRAINT fk_content_upload_task_creator FOREIGN KEY (
        creator_id
    ) REFERENCES creator_profile (id),
    CONSTRAINT fk_content_upload_task_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_content_upload_task_track FOREIGN KEY (
        track_id
    ) REFERENCES audio_track (id),
    CONSTRAINT fk_content_upload_task_file FOREIGN KEY (
        file_id
    ) REFERENCES track_audio_file (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容上传任务表';

CREATE TABLE content_audit_record (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    audit_no VARCHAR(64) NOT NULL,
    upload_task_id BIGINT NULL,
    target_type VARCHAR(32) NOT NULL COMMENT
    '枚举：album,track,audio_file,upload_task,comment,creator_profile',
    target_id BIGINT NOT NULL,
    audit_type VARCHAR(16) NOT NULL COMMENT '枚举：machine,manual,appeal',
    audit_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT
    '枚举：pending,approved,rejected,need_modify,blocked',
    audit_reason VARCHAR(500) NULL,
    audit_payload JSON NULL,
    auditor_name VARCHAR(64) NULL,
    audited_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_audit_record_no (audit_no),
    KEY idx_content_audit_record_target (target_type, target_id),
    CONSTRAINT fk_content_audit_record_upload_task FOREIGN KEY (
        upload_task_id
    ) REFERENCES content_upload_task (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容审核记录表';

CREATE TABLE album_update_record (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    album_id BIGINT NOT NULL,
    track_id BIGINT NULL,
    creator_id BIGINT NULL,
    update_type VARCHAR(32) NOT NULL COMMENT
    '枚举：album_published,track_published,batch_tracks_published,resume_update,pause_update,completed,offline',
    update_title VARCHAR(255) NOT NULL,
    update_summary TEXT NULL,
    track_count_delta INT NOT NULL DEFAULT 0,
    duration_delta_seconds BIGINT NOT NULL DEFAULT 0,
    updated_at_event DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_album_update_record_album (album_id, updated_at_event),
    CONSTRAINT fk_album_update_record_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_album_update_record_track FOREIGN KEY (
        track_id
    ) REFERENCES audio_track (id),
    CONSTRAINT fk_album_update_record_creator FOREIGN KEY (
        creator_id
    ) REFERENCES creator_profile (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '专辑更新记录表';

CREATE TABLE album_price_rule (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    album_id BIGINT NOT NULL,
    price_type VARCHAR(32) NOT NULL COMMENT
    '枚举：free,vip_free,album_paid,track_paid,limited_free',
    currency_code VARCHAR(10) NOT NULL,
    album_price_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    track_price_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    free_track_count INT NOT NULL DEFAULT 0,
    effective_from DATETIME NOT NULL,
    effective_to DATETIME NULL,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_album_price_rule_period (
        album_id, price_type, effective_from
    ),
    CONSTRAINT fk_album_price_rule_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_album_price_rule_currency FOREIGN KEY (
        currency_code
    ) REFERENCES dim_currency (currency_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '专辑价格规则表';

CREATE TABLE play_session (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_no VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    album_id BIGINT NOT NULL,
    track_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    start_position_seconds INT NOT NULL DEFAULT 0,
    end_position_seconds INT NOT NULL DEFAULT 0,
    played_seconds INT NOT NULL DEFAULT 0,
    play_start_at DATETIME NOT NULL,
    play_end_at DATETIME NULL,
    play_status VARCHAR(32) NOT NULL COMMENT '枚举：completed,interrupted,failed',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_play_session_no (session_no),
    KEY idx_play_session_user_track (user_id, track_id, play_start_at),
    CONSTRAINT fk_play_session_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_play_session_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_play_session_track FOREIGN KEY (
        track_id
    ) REFERENCES audio_track (id),
    CONSTRAINT fk_play_session_channel FOREIGN KEY (
        channel_id
    ) REFERENCES dim_channel (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '播放会话表';

CREATE TABLE listening_progress (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    album_id BIGINT NOT NULL,
    track_id BIGINT NOT NULL,
    position_seconds INT NOT NULL DEFAULT 0,
    duration_seconds INT NOT NULL DEFAULT 0,
    finished_flag TINYINT NOT NULL DEFAULT 0,
    last_played_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_listening_progress_track (user_id, track_id),
    CONSTRAINT fk_listening_progress_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_listening_progress_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_listening_progress_track FOREIGN KEY (
        track_id
    ) REFERENCES audio_track (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '收听进度表';

CREATE TABLE content_comment (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    target_type VARCHAR(16) NOT NULL COMMENT '枚举：album,track',
    target_id BIGINT NOT NULL,
    parent_comment_id BIGINT NULL,
    comment_text TEXT NOT NULL,
    audit_status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT
    '枚举：pending,approved,rejected',
    like_count BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_content_comment_target (target_type, target_id, created_at),
    CONSTRAINT fk_content_comment_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_content_comment_parent FOREIGN KEY (
        parent_comment_id
    ) REFERENCES content_comment (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容评论表';

CREATE TABLE content_rating (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    album_id BIGINT NOT NULL,
    rating_score DECIMAL(4, 2) NOT NULL,
    rating_text VARCHAR(500) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_rating_user_album (user_id, album_id),
    CONSTRAINT fk_content_rating_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_content_rating_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容评分表';

CREATE TABLE user_reaction (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    target_type VARCHAR(32) NOT NULL COMMENT '枚举：album,track,comment,narrator',
    target_id BIGINT NOT NULL,
    reaction_type VARCHAR(16) NOT NULL COMMENT '枚举：like,dislike,share,forward',
    reaction_status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT '枚举：active,cancelled',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_reaction_key (
        user_id, target_type, target_id, reaction_type
    ),
    KEY idx_user_reaction_target (target_type, target_id),
    CONSTRAINT fk_user_reaction_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户互动表';

CREATE TABLE content_report (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    report_no VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    target_type VARCHAR(32) NOT NULL COMMENT '枚举：album,track,comment,narrator',
    target_id BIGINT NOT NULL,
    report_reason VARCHAR(32) NOT NULL COMMENT
    '枚举：copyright,illegal,violent,pornographic,spam,other',
    report_text TEXT NULL,
    handle_status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT
    '枚举：pending,accepted,rejected,closed',
    handled_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_report_no (report_no),
    KEY idx_content_report_target (target_type, target_id),
    CONSTRAINT fk_content_report_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容举报表';

CREATE TABLE user_activity_feed (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    feed_no VARCHAR(64) NOT NULL,
    actor_user_id BIGINT NULL,
    creator_id BIGINT NULL,
    feed_type VARCHAR(32) NOT NULL COMMENT
    '枚举：publish_album,publish_program,publish_track,update_album,delete_resource,follow,system_notice',
    target_type VARCHAR(32) NOT NULL DEFAULT 'none' COMMENT
    '枚举：none,album,track,narrator,organization,topic',
    target_id BIGINT NULL,
    feed_title VARCHAR(255) NOT NULL,
    feed_content TEXT NULL,
    visibility VARCHAR(16) NOT NULL DEFAULT 'public' COMMENT
    '枚举：public,followers,private,deleted',
    published_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_activity_feed_no (feed_no),
    CONSTRAINT fk_user_activity_feed_actor FOREIGN KEY (
        actor_user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_user_activity_feed_creator FOREIGN KEY (
        creator_id
    ) REFERENCES creator_profile (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '用户动态表';

CREATE TABLE support_ticket (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    ticket_no VARCHAR(64) NOT NULL,
    user_id BIGINT NULL,
    ticket_type VARCHAR(32) NOT NULL COMMENT
    '枚举：feature_feedback,usage_feedback,copyright_complaint,payment_issue,account_issue,content_issue,other',
    related_type VARCHAR(32) NOT NULL DEFAULT 'none' COMMENT
    '枚举：none,album,track,content_order,recharge_order,payment,refund,upload_task,report',
    related_id BIGINT NULL,
    ticket_title VARCHAR(255) NOT NULL,
    ticket_content TEXT NOT NULL,
    contact_mobile VARCHAR(32) NULL,
    contact_email VARCHAR(128) NULL,
    ticket_status VARCHAR(32) NOT NULL DEFAULT 'submitted' COMMENT
    '枚举：submitted,processing,waiting_user,resolved,rejected,closed',
    handle_result TEXT NULL,
    submitted_at DATETIME NOT NULL,
    handled_at DATETIME NULL,
    closed_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_support_ticket_no (ticket_no),
    CONSTRAINT fk_support_ticket_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '反馈工单表';

CREATE TABLE vip_plan (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    plan_code VARCHAR(64) NOT NULL,
    plan_name VARCHAR(128) NOT NULL,
    member_level VARCHAR(16) NOT NULL COMMENT '枚举：vip,svip',
    duration_days INT NOT NULL,
    currency_code VARCHAR(10) NOT NULL,
    sale_price_amount DECIMAL(12, 2) NOT NULL,
    original_price_amount DECIMAL(12, 2) NOT NULL,
    benefit_payload JSON NULL,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_vip_plan_code (plan_code),
    CONSTRAINT fk_vip_plan_currency FOREIGN KEY (
        currency_code
    ) REFERENCES dim_currency (currency_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'VIP套餐表';

CREATE TABLE wallet_account (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    currency_code VARCHAR(10) NOT NULL,
    wallet_status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT '枚举：active,frozen,closed',
    balance_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    frozen_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    available_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    opened_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_wallet_account_user_currency (user_id, currency_code),
    CONSTRAINT ck_wallet_account_amount CHECK (
        balance_amount >= 0
        AND frozen_amount >= 0
        AND available_amount >= 0
        AND ROUND(balance_amount - frozen_amount, 2) = available_amount
    ),
    CONSTRAINT fk_wallet_account_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_wallet_account_currency FOREIGN KEY (
        currency_code
    ) REFERENCES dim_currency (currency_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '钱包账户表';

CREATE TABLE recharge_order (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    recharge_no VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    wallet_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    currency_code VARCHAR(10) NOT NULL,
    recharge_amount DECIMAL(12, 2) NOT NULL,
    gift_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    payable_amount DECIMAL(12, 2) NOT NULL,
    recharge_status VARCHAR(32) NOT NULL DEFAULT 'created' COMMENT
    '枚举：created,paying,paid,credited,cancelled,failed,refunded',
    paid_at DATETIME NULL,
    credited_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_recharge_order_no (recharge_no),
    KEY idx_recharge_order_status (recharge_status),
    CONSTRAINT fk_recharge_order_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_recharge_order_wallet FOREIGN KEY (
        wallet_id
    ) REFERENCES wallet_account (id),
    CONSTRAINT fk_recharge_order_channel FOREIGN KEY (
        channel_id
    ) REFERENCES dim_channel (id),
    CONSTRAINT fk_recharge_order_currency FOREIGN KEY (
        currency_code
    ) REFERENCES dim_currency (currency_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '充值订单表';

CREATE TABLE content_order (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_no VARCHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    currency_code VARCHAR(10) NOT NULL,
    order_type VARCHAR(16) NOT NULL COMMENT '枚举：vip,album,track,bundle',
    order_status VARCHAR(32) NOT NULL DEFAULT 'created' COMMENT
    '枚举：created,paid,cancelled,refunding,refunded,closed',
    total_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    discount_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    payable_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    paid_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_order_no (order_no),
    CONSTRAINT fk_content_order_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_content_order_channel FOREIGN KEY (
        channel_id
    ) REFERENCES dim_channel (id),
    CONSTRAINT fk_content_order_currency FOREIGN KEY (
        currency_code
    ) REFERENCES dim_currency (currency_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容订单表';

CREATE TABLE content_order_item (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    order_id BIGINT NOT NULL,
    item_type VARCHAR(16) NOT NULL COMMENT '枚举：vip_plan,album,track',
    vip_plan_id BIGINT NULL,
    album_id BIGINT NULL,
    track_id BIGINT NULL,
    item_name VARCHAR(255) NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    unit_price_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    discount_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    payable_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    item_target_key VARCHAR(128) GENERATED ALWAYS AS (
        CASE
            WHEN item_type = 'vip_plan' THEN CONCAT('vip_plan:', vip_plan_id)
            WHEN item_type = 'album' THEN CONCAT('album:', album_id)
            WHEN item_type = 'track' THEN CONCAT('track:', track_id)
            ELSE CONCAT(
                item_type,
                ':',
                IFNULL(vip_plan_id, 0),
                ':',
                IFNULL(album_id, 0),
                ':',
                IFNULL(track_id, 0)
            )
        END
    ) STORED,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_order_item_target (
        order_id, item_type, item_target_key
    ),
    CONSTRAINT ck_content_order_item_target CHECK (
        (
            item_type = 'vip_plan'
            AND vip_plan_id IS NOT NULL
            AND album_id IS NULL
            AND track_id IS NULL
        )
        OR (
            item_type = 'album'
            AND vip_plan_id IS NULL
            AND album_id IS NOT NULL
            AND track_id IS NULL
        )
        OR (
            item_type = 'track'
            AND vip_plan_id IS NULL
            AND album_id IS NULL
            AND track_id IS NOT NULL
        )
    ),
    CONSTRAINT fk_content_order_item_order FOREIGN KEY (
        order_id
    ) REFERENCES content_order (id),
    CONSTRAINT fk_content_order_item_vip_plan FOREIGN KEY (
        vip_plan_id
    ) REFERENCES vip_plan (id),
    CONSTRAINT fk_content_order_item_album FOREIGN KEY (
        album_id
    ) REFERENCES audio_album (id),
    CONSTRAINT fk_content_order_item_track FOREIGN KEY (
        track_id
    ) REFERENCES audio_track (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容订单明细表';

CREATE TABLE payment_record (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    payment_no VARCHAR(64) NOT NULL,
    pay_subject_type VARCHAR(32) NOT NULL COMMENT '枚举：content_order,recharge_order',
    pay_subject_id BIGINT NOT NULL,
    payment_channel VARCHAR(32) NOT NULL COMMENT
    '枚举：wechat_pay,alipay,apple_pay,balance,coupon',
    currency_code VARCHAR(10) NOT NULL,
    payment_amount DECIMAL(12, 2) NOT NULL DEFAULT 0,
    payment_status VARCHAR(32) NOT NULL DEFAULT 'created' COMMENT
    '枚举：created,processing,success,failed,closed',
    paid_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_payment_record_no (payment_no),
    KEY idx_payment_record_subject (pay_subject_type, pay_subject_id),
    KEY idx_payment_record_status_channel_subject (
        payment_status, payment_channel, pay_subject_type, pay_subject_id
    ),
    CONSTRAINT ck_payment_record_channel CHECK (
        (
            pay_subject_type = 'recharge_order'
            AND payment_channel IN ('wechat_pay', 'alipay', 'apple_pay')
        )
        OR (
            pay_subject_type = 'content_order'
            AND payment_channel IN (
                'wechat_pay', 'alipay', 'apple_pay', 'balance', 'coupon'
            )
        )
    ),
    CONSTRAINT fk_payment_record_currency FOREIGN KEY (
        currency_code
    ) REFERENCES dim_currency (currency_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '支付流水表';

CREATE TABLE refund_record (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    refund_no VARCHAR(64) NOT NULL,
    refund_subject_type VARCHAR(32) NOT NULL COMMENT '枚举：content_order,recharge_order',
    refund_subject_id BIGINT NOT NULL,
    payment_id BIGINT NOT NULL,
    refund_reason VARCHAR(500) NULL,
    refund_amount DECIMAL(12, 2) NOT NULL,
    refund_status VARCHAR(32) NOT NULL DEFAULT 'requested' COMMENT
    '枚举：requested,approved,rejected,success,failed',
    requested_at DATETIME NOT NULL,
    handled_at DATETIME NULL,
    refunded_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_refund_record_no (refund_no),
    KEY idx_refund_record_subject (refund_subject_type, refund_subject_id),
    CONSTRAINT fk_refund_record_payment FOREIGN KEY (
        payment_id
    ) REFERENCES payment_record (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '退款单表';

CREATE TABLE refund_record_item (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    refund_id BIGINT NOT NULL,
    order_item_id BIGINT NOT NULL,
    item_type VARCHAR(16) NOT NULL COMMENT '枚举：vip_plan,album,track',
    refund_quantity INT NOT NULL DEFAULT 1,
    refund_amount DECIMAL(12, 2) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_refund_record_item_order_item (refund_id, order_item_id),
    CONSTRAINT fk_refund_record_item_refund FOREIGN KEY (
        refund_id
    ) REFERENCES refund_record (id),
    CONSTRAINT fk_refund_record_item_order_item FOREIGN KEY (
        order_item_id
    ) REFERENCES content_order_item (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '退款明细表';

CREATE TABLE entitlement_record (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    source_type VARCHAR(16) NOT NULL COMMENT '枚举：vip,purchase,gift,promotion',
    order_id BIGINT NULL,
    target_type VARCHAR(16) NOT NULL COMMENT '枚举：vip,album,track',
    target_id BIGINT NOT NULL,
    valid_from DATETIME NOT NULL,
    valid_to DATETIME NULL,
    entitlement_status VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT
    '枚举：active,expired,revoked',
    source_key VARCHAR(128) GENERATED ALWAYS AS (
        CONCAT(source_type, ':', IFNULL(order_id, 0))
    ) STORED,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_entitlement_record_key (
        user_id, target_type, target_id, source_type, source_key
    ),
    KEY idx_entitlement_record_target (target_type, target_id),
    CONSTRAINT ck_entitlement_record_source CHECK (
        (
            source_type IN ('vip', 'purchase')
            AND order_id IS NOT NULL
        )
        OR (
            source_type IN ('gift', 'promotion')
        )
    ),
    CONSTRAINT fk_entitlement_record_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_entitlement_record_order FOREIGN KEY (
        order_id
    ) REFERENCES content_order (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '权益记录表';

CREATE TABLE wallet_ledger (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    ledger_no VARCHAR(64) NOT NULL,
    wallet_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    ledger_type VARCHAR(16) NOT NULL COMMENT
    '枚举：recharge,consume,refund,freeze,unfreeze,adjust',
    related_type VARCHAR(32) NOT NULL COMMENT
    '枚举：recharge_order,content_order,payment,refund,manual',
    related_id BIGINT NOT NULL,
    currency_code VARCHAR(10) NOT NULL,
    amount_delta DECIMAL(12, 2) NOT NULL DEFAULT 0,
    frozen_delta DECIMAL(12, 2) NOT NULL DEFAULT 0,
    balance_after DECIMAL(12, 2) NOT NULL DEFAULT 0,
    frozen_after DECIMAL(12, 2) NOT NULL DEFAULT 0,
    available_after DECIMAL(12, 2) NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_wallet_ledger_no (ledger_no),
    KEY idx_wallet_ledger_wallet (wallet_id, created_at, id),
    CONSTRAINT ck_wallet_ledger_amount CHECK (
        balance_after >= 0
        AND frozen_after >= 0
        AND available_after >= 0
        AND ROUND(balance_after - frozen_after, 2) = available_after
    ),
    CONSTRAINT fk_wallet_ledger_wallet FOREIGN KEY (
        wallet_id
    ) REFERENCES wallet_account (id),
    CONSTRAINT fk_wallet_ledger_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_wallet_ledger_currency FOREIGN KEY (
        currency_code
    ) REFERENCES dim_currency (currency_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '钱包流水表';

CREATE TABLE ranking_list (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    ranking_code VARCHAR(64) NOT NULL,
    ranking_name VARCHAR(128) NOT NULL,
    ranking_type VARCHAR(32) NOT NULL COMMENT
    '枚举：hot_album,new_album,completed_album,paid_album,narrator',
    category_id BIGINT NULL,
    period_type VARCHAR(16) NOT NULL COMMENT '枚举：daily,weekly,monthly,total',
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_ranking_list_code (ranking_code),
    CONSTRAINT fk_ranking_list_category FOREIGN KEY (
        category_id
    ) REFERENCES dim_audio_category (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '榜单表';

CREATE TABLE ranking_item (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    ranking_id BIGINT NOT NULL,
    stat_date DATE NOT NULL,
    target_type VARCHAR(16) NOT NULL COMMENT '枚举：album,narrator',
    target_id BIGINT NOT NULL,
    rank_no INT NOT NULL,
    score_value DECIMAL(18, 4) NOT NULL DEFAULT 0,
    play_count BIGINT NOT NULL DEFAULT 0,
    favorite_count BIGINT NOT NULL DEFAULT 0,
    order_count BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_ranking_item_rank (ranking_id, stat_date, rank_no),
    UNIQUE KEY uk_ranking_item_target (
        ranking_id, stat_date, target_type, target_id
    ),
    CONSTRAINT fk_ranking_item_ranking FOREIGN KEY (
        ranking_id
    ) REFERENCES ranking_list (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '榜单明细表';

CREATE TABLE recommend_slot (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    slot_code VARCHAR(64) NOT NULL,
    slot_name VARCHAR(128) NOT NULL,
    page_code VARCHAR(32) NOT NULL COMMENT '枚举：home,category,album_detail,player,search',
    slot_type VARCHAR(32) NOT NULL COMMENT
    '枚举：banner,album_list,narrator_list,topic_list,rank_entry',
    max_item_count INT NOT NULL DEFAULT 1,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_recommend_slot_code (slot_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '推荐位表';

CREATE TABLE content_topic (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    topic_code VARCHAR(64) NOT NULL,
    topic_title VARCHAR(255) NOT NULL,
    topic_type VARCHAR(32) NOT NULL COMMENT
    '枚举：editorial,promotion,category,festival',
    cover_url VARCHAR(500) NULL,
    summary TEXT NULL,
    topic_status VARCHAR(16) NOT NULL DEFAULT 'draft' COMMENT '枚举：draft,published,offline',
    published_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_topic_code (topic_code)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '内容专题表';

CREATE TABLE recommend_item (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    slot_id BIGINT NOT NULL,
    target_type VARCHAR(16) NOT NULL COMMENT '枚举：album,narrator,topic,ranking,url',
    target_id BIGINT NULL,
    title VARCHAR(255) NULL,
    image_url VARCHAR(500) NULL,
    jump_url VARCHAR(500) NULL,
    sort_no INT NOT NULL DEFAULT 0,
    effective_from DATETIME NOT NULL,
    effective_to DATETIME NULL,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_recommend_item_sort (slot_id, sort_no, effective_from),
    KEY idx_recommend_item_target (target_type, target_id),
    CONSTRAINT fk_recommend_item_slot FOREIGN KEY (
        slot_id
    ) REFERENCES recommend_slot (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '推荐明细表';

CREATE TABLE content_topic_item (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    topic_id BIGINT NOT NULL,
    target_type VARCHAR(16) NOT NULL COMMENT '枚举：album,narrator,ranking',
    target_id BIGINT NOT NULL,
    title VARCHAR(255) NULL,
    summary TEXT NULL,
    image_url VARCHAR(500) NULL,
    sort_no INT NOT NULL DEFAULT 0,
    yn TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_content_topic_item_target (
        topic_id, target_type, target_id
    ),
    UNIQUE KEY uk_content_topic_item_sort (topic_id, sort_no),
    CONSTRAINT fk_content_topic_item_topic FOREIGN KEY (
        topic_id
    ) REFERENCES content_topic (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '专题明细表';

CREATE TABLE search_query_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    query_no VARCHAR(64) NOT NULL,
    user_id BIGINT NULL,
    channel_id BIGINT NOT NULL,
    keyword VARCHAR(255) NOT NULL,
    search_type VARCHAR(16) NOT NULL COMMENT
    '枚举：all,album,book,program,track,narrator,organization,topic',
    result_count INT NOT NULL DEFAULT 0,
    clicked_flag TINYINT NOT NULL DEFAULT 0,
    clicked_target_type VARCHAR(32) NOT NULL DEFAULT 'none' COMMENT
    '枚举：none,album,track,narrator,organization,topic',
    clicked_target_id BIGINT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_search_query_log_no (query_no),
    KEY idx_search_query_log_keyword (keyword, created_at),
    CONSTRAINT fk_search_query_log_user FOREIGN KEY (
        user_id
    ) REFERENCES user_account (id),
    CONSTRAINT fk_search_query_log_channel FOREIGN KEY (
        channel_id
    ) REFERENCES dim_channel (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '搜索明细日志表';

CREATE TABLE search_keyword_stat (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    stat_date DATE NOT NULL,
    channel_id BIGINT NOT NULL,
    keyword VARCHAR(255) NOT NULL,
    search_count BIGINT NOT NULL DEFAULT 0,
    result_click_count BIGINT NOT NULL DEFAULT 0,
    album_click_count BIGINT NOT NULL DEFAULT 0,
    narrator_click_count BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_search_keyword_stat_key (stat_date, channel_id, keyword),
    CONSTRAINT fk_search_keyword_stat_channel FOREIGN KEY (
        channel_id
    ) REFERENCES dim_channel (id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = '搜索词统计表';
