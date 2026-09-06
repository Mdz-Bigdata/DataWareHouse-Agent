<script setup lang="ts">
import { ref, onMounted, watch, computed, nextTick } from "vue";
import { metadataApi } from "../../api/metadata";
import type { Relationship, Table, AllTablesDataset } from "../../api/metadata";
import { useUser } from "../../composables/useUser";

const { isAdmin: _isAdmin, hasPermission } = useUser();

const props = defineProps<{
  datasetId: number;
  tables: Table[];
}>();

const relationships = ref<Relationship[]>([]);
const loading = ref(false);
const showModal = ref(false);
const saving = ref(false);

// UI State
const error = ref("");
const modalError = ref("");
const editingId = ref<number | null>(null);
const deleteId = ref<number | null>(null);

const form = ref<Relationship>({
  source_table_id: 0,
  target_table_id: 0,
  join_condition: "",
  join_type: "left",
  description: "",
});

// Field Selection Logic
const sourceField = ref("");
const targetField = ref("");

// 跨数据集：全平台所有数据集 + 表列表
const allTablesList = ref<AllTablesDataset[]>([]);

const sourceColumns = computed(() => {
  const table = props.tables.find((t) => t.id === form.value.source_table_id);
  return table ? table.columns : [];
});

// targetColumns 改为从 allTablesList 中查找，支持跨数据集
const targetColumns = computed(() => {
  for (const ds of allTablesList.value) {
    const found = ds.tables.find((t) => t.id === form.value.target_table_id);
    if (found) {
      // 优先从 allTablesList (可能包含跨数据集的 columns) 中获取
      if (found.columns && found.columns.length > 0) {
        return found.columns;
      }
      // 先看 props.tables 有没有（同数据集优先）
      const localTable = props.tables.find((t) => t.id === form.value.target_table_id);
      return localTable ? localTable.columns : [];
    }
  }
  const table = props.tables.find((t) => t.id === form.value.target_table_id);
  return table ? table.columns : [];
});

const applyJoinCondition = () => {
  if (sourceField.value && targetField.value) {
    const sourceTable = props.tables.find(
      (t) => t.id === form.value.source_table_id
    );
    // 目标表从 allTablesList 中找
    let targetPhysicalName = "";
    for (const ds of allTablesList.value) {
      const found = ds.tables.find((t) => t.id === form.value.target_table_id);
      if (found) {
        targetPhysicalName = found.physical_name;
        break;
      }
    }
    if (!targetPhysicalName) {
      const localTarget = props.tables.find((t) => t.id === form.value.target_table_id);
      if (localTarget) targetPhysicalName = localTarget.physical_name;
    }
    if (sourceTable && targetPhysicalName) {
      form.value.join_condition = `${sourceTable.physical_name}.${sourceField.value} = ${targetPhysicalName}.${targetField.value}`;
    }
  }
};

// Reset fields when tables change
watch(
  () => form.value.source_table_id,
  () => {
    sourceField.value = "";
  }
);
watch(
  () => form.value.target_table_id,
  () => {
    targetField.value = "";
  }
);

const fetchRelationships = async () => {
  loading.value = true;
  error.value = "";
  try {
    const res = await metadataApi.getRelationships(props.datasetId);
    relationships.value = res.data;
  } catch (e) {
    console.error(e);
    error.value = "无法加载关系列表";
  } finally {
    loading.value = false;
  }
};

// 获取全平台表列表（用于跨数据集目标表选择）
const fetchAllTables = async () => {
  try {
    const res = await metadataApi.getAllTables();
    allTablesList.value = res.data;
  } catch (e) {
    console.error("[RelationshipList] Failed to fetch all tables:", e);
  }
};

const openCreate = () => {
  editingId.value = null;
  const firstTable = props.tables[0];
  const defaultId = firstTable && firstTable.id ? firstTable.id : 0;
  form.value = {
    source_table_id: defaultId,
    target_table_id: defaultId,
    join_condition: "",
    join_type: "left",
    description: "",
  };
  modalError.value = "";
  showModal.value = true;
};

const openEdit = (r: Relationship) => {
  editingId.value = r.id || null;
  form.value = { ...r };
  
  // Use nextTick to ensure watchers (which reset fields on ID change) 
  // run first before we set the parsed field values.
  nextTick(() => {
    if (r.join_condition) {
      try {
        const parts = r.join_condition.split("=");
        if (parts.length === 2) {
          const leftPart = parts[0]!.trim();
          const rightPart = parts[1]!.trim();
          
          const leftField = leftPart.split(".").pop();
          const rightField = rightPart.split(".").pop();
          
          if (leftField) sourceField.value = leftField;
          if (rightField) targetField.value = rightField;
        }
      } catch (e) {
        console.warn("Failed to parse join condition for UI select", e);
      }
    }
  });
  
  modalError.value = "";
  showModal.value = true;
};

const handleDelete = (id: number) => {
  deleteId.value = id;
};

const confirmDelete = async () => {
  if (!deleteId.value) return;
  try {
    await metadataApi.deleteRelationship(deleteId.value);
    deleteId.value = null;
    fetchRelationships();
  } catch (e) {
    console.error(e);
    error.value = "删除失败";
    setTimeout(() => (error.value = ""), 3000);
    deleteId.value = null;
  }
};

const handleSave = async () => {
  modalError.value = "";
  if (
    !form.value.source_table_id ||
    !form.value.target_table_id ||
    !form.value.join_condition
  ) {
    modalError.value = "请填写必要信息 (源表, 目标表, 关联条件)";
    return;
  }
  saving.value = true;
  try {
    if (editingId.value) {
      await metadataApi.updateRelationship(editingId.value, form.value);
    } else {
      await metadataApi.createRelationship(props.datasetId, form.value);
    }
    showModal.value = false;
    fetchRelationships();
  } catch (e: any) {
    console.error(e);
    modalError.value = "保存失败: " + (e.response?.data?.detail || e.message);
  } finally {
    saving.value = false;
  }
};

// 判断某个 table_id 是否属于当前数据集（源数据集）
const isCurrentDataset = (tableId: number) => {
  return props.tables.some((t) => t.id === tableId);
};

// 获取表名（支持跨数据集，回显格式为 "数据集名.表名"）
const getTableName = (id: number) => {
  // 先在当前数据集找
  const local = props.tables.find((t) => t.id === id);
  if (local) return local.physical_name + (local.term ? ` (${local.term})` : "");
  // 再在跨数据集列表找
  for (const ds of allTablesList.value) {
    const t = ds.tables.find((t) => t.id === id);
    if (t) return `${ds.dataset_name}.${t.physical_name}${t.term ? ` (${t.term})` : ""}`;
  }
  return `Unknown(${id})`;
};

watch(() => props.datasetId, fetchRelationships);
onMounted(() => {
  fetchRelationships();
  fetchAllTables();
});
</script>

<template>
  <div class="space-y-4">
    <!-- Toolbar -->
    <div class="flex justify-between items-center">
      <h3 class="text-lg font-bold text-gray-800">实体关系 (Relationships)</h3>
      <button
        v-if="hasPermission('element:metadata:edit')"
        @click="openCreate"
        class="bg-purple-600 hover:bg-purple-700 text-white px-4 py-2 rounded-lg transition-all shadow-md flex items-center gap-2 text-sm font-bold"
      >
        <svg
          class="w-4 h-4"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            d="M12 4v16m8-8H4"
          />
        </svg>
        新建关系
      </button>
    </div>

    <div
      v-if="error"
      class="bg-red-50 text-red-600 px-4 py-2 rounded-lg text-sm mb-4 border border-red-100 flex items-center gap-2"
    >
      <svg
        class="w-4 h-4 shrink-0"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          stroke-width="2"
          d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
        />
      </svg>
      {{ error }}
    </div>

    <!-- List -->
    <div v-if="loading" class="flex justify-center py-8">
      <div
        class="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-600"
      ></div>
    </div>

    <div
      v-else-if="relationships.length === 0"
      class="text-center py-12 bg-purple-50/50 rounded-xl border border-dashed border-purple-200"
    >
      <p class="text-purple-700 font-medium">暂无定义的实体关系</p>
      <p class="text-xs text-purple-500 mt-1">
        定义表之间的 Join 路径，让 AI 知道如何关联查询。
      </p>
    </div>

    <div v-else class="space-y-3">
      <div
        v-for="r in relationships"
        :key="r.id"
        class="bg-white p-4 rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-all group flex flex-col xl:flex-row xl:items-center gap-4"
      >
        <!-- Relationship Visual -->
        <div
          class="flex-1 flex flex-col md:flex-row items-center justify-center xl:justify-start gap-3 min-w-0"
        >
          <!-- 跨数据集徽章 -->
          <span
            v-if="!isCurrentDataset(r.target_table_id)"
            class="shrink-0 px-2 py-1 text-[10px] font-bold bg-amber-500 text-white rounded shadow-sm uppercase tracking-wider"
          >
            跨数据集
          </span>
          <!-- Source -->
          <div
            class="px-3 py-2 bg-blue-50 text-blue-700 rounded-lg text-xs font-mono border border-blue-100 flex items-center gap-2 max-w-full md:max-w-[240px] truncate"
            :title="getTableName(r.source_table_id)"
          >
            <svg
              class="w-4 h-4 text-blue-400 shrink-0"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"
              />
            </svg>
            <span class="truncate">{{ getTableName(r.source_table_id) }}</span>
          </div>

          <!-- Connector -->
          <div class="flex flex-col items-center shrink-0">
            <span
              class="text-[10px] text-purple-500 font-bold uppercase tracking-wider bg-purple-50 px-2 py-0.5 rounded border border-purple-100 whitespace-nowrap"
              >{{ r.join_type }}</span
            >
            <svg
              class="w-6 h-6 text-gray-300"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M14 5l7 7m0 0l-7 7m7-7H3"
              />
            </svg>
          </div>

          <!-- Target -->
          <div
            class="px-3 py-2 bg-green-50 text-green-700 rounded-lg text-xs font-mono border border-green-100 flex items-center gap-2 max-w-full md:max-w-[240px] truncate"
            :title="getTableName(r.target_table_id)"
          >
            <svg
              class="w-4 h-4 text-green-400 shrink-0"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"
              />
            </svg>
            <span class="truncate">{{ getTableName(r.target_table_id) }}</span>
            <!-- 跨数据集标记 -->
            <span
              v-if="!isCurrentDataset(r.target_table_id)"
              class="ml-1 px-1.5 py-0.5 text-[9px] font-bold bg-amber-100 text-amber-700 rounded border border-amber-200 whitespace-nowrap shrink-0"
            >跨库</span>
          </div>
        </div>

        <!-- Join Condition -->
        <div class="flex-1 min-w-0 text-center xl:text-left">
          <div class="relative group/code text-left">
            <code
              class="block w-full text-xs bg-gray-900 text-green-400 font-mono px-3 py-2 rounded-lg break-all md:truncate hover:white-space-normal transition-all cursor-pointer"
              :title="r.join_condition"
              @click="openEdit(r)"
            >
              {{ r.join_condition }}
            </code>
            <!-- Tooltip hint -->
            <span
              v-if="hasPermission('element:metadata:edit')"
              class="absolute top-0 right-0 p-1 text-[10px] text-gray-500 opacity-0 group-hover/code:opacity-100 transition-opacity"
              >Click to Edit</span
            >
          </div>
        </div>

        <!-- Actions -->
        <div
          class="flex items-center gap-2 shrink-0 justify-end w-full xl:w-auto mt-2 xl:mt-0 pt-2 xl:pt-0 border-t xl:border-0 border-gray-100"
          v-if="hasPermission('element:metadata:edit')"
        >
          <span
            class="text-xs text-gray-400 truncate max-w-[150px] mr-2"
            :title="r.description"
            >{{ r.description || "No desc" }}</span
          >

          <button
            @click="openEdit(r)"
            class="text-gray-400 hover:text-blue-500 p-2 bg-white hover:bg-blue-50 rounded transition-colors"
            title="Edit Relationship"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"
              />
            </svg>
          </button>
          <button
            v-if="hasPermission('element:metadata:delete_table')"
            @click="handleDelete(r.id!)"
            class="text-gray-400 hover:text-red-500 p-2 bg-white hover:bg-red-50 rounded transition-colors"
            title="Delete Relationship"
          >
            <svg
              class="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
              />
            </svg>
          </button>
        </div>
        <div
          v-else
          class="flex items-center gap-2 shrink-0 justify-end w-full xl:w-auto mt-2 xl:mt-0 pt-2 xl:pt-0 border-t xl:border-0 border-gray-100"
        >
          <span
            class="text-xs text-gray-400 truncate max-w-[300px]"
            :title="r.description"
            >{{ r.description || "No description" }}</span
          >
        </div>
      </div>
    </div>

    <!-- Modal -->
    <div
      v-if="showModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      @click.self="showModal = false"
    >
      <div
        class="bg-white rounded-xl shadow-2xl w-full max-w-lg overflow-hidden border border-gray-100 animate-fade-in-up"
      >
        <div
          class="p-6 border-b border-gray-100 flex justify-between items-center bg-purple-50"
        >
          <h3 class="font-bold text-gray-900">
            {{ editingId ? "编辑关联关系" : "新建关联关系" }}
          </h3>
          <button
            @click="showModal = false"
            class="text-gray-400 hover:text-gray-600"
          >
            &times;
          </button>
        </div>

        <!-- Error in Modal -->
        <div v-if="modalError" class="px-6 pt-4 pb-0">
          <div
            class="bg-red-50 text-red-600 px-3 py-2 rounded text-xs border border-red-100"
          >
            {{ modalError }}
          </div>
        </div>

        <div class="p-6 space-y-4">
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1"
                >源表 (Left)</label
              >
              <select
                v-model="form.source_table_id"
                class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 focus:outline-none"
              >
                <option v-for="t in tables" :key="t.id" :value="t.id">
                  {{ t.physical_name }} {{ t.term ? `(${t.term})` : "" }}
                </option>
              </select>
              <!-- Field Selector -->
              <div class="mt-2">
                <label
                  class="block text-[10px] uppercase font-bold text-gray-400 mb-1"
                  >关联字段</label
                >
                <select
                  v-model="sourceField"
                  @change="applyJoinCondition"
                  class="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-purple-400 outline-none bg-gray-50/50"
                >
                  <option value="">-- 选择字段 --</option>
                  <option
                    v-for="col in sourceColumns"
                    :key="col.physical_name"
                    :value="col.physical_name"
                  >
                    {{ col.physical_name }}
                    {{ col.term ? `[${col.term}]` : "" }}
                  </option>
                </select>
              </div>
            </div>
            <div>
              <label class="block text-sm font-medium text-gray-700 mb-1"
                >目标表 (Right)</label
              >
              <!-- 跨数据集分组下拉 -->
              <select
                v-model="form.target_table_id"
                class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 focus:outline-none"
              >
                <!-- 当前数据集（同库关联）-->
                <optgroup :label="'当前数据集'">
                  <option v-for="t in tables" :key="t.id" :value="t.id">
                    {{ t.physical_name }} {{ t.term ? `(${t.term})` : "" }}
                  </option>
                </optgroup>
                <!-- 其他数据集（跨库关联）-->
                <template v-for="ds in allTablesList" :key="ds.dataset_id">
                  <optgroup
                    v-if="ds.tables.some(t => !tables.find(lt => lt.id === t.id))"
                    :label="`${ds.display_name} [跨数据集]`"
                  >
                    <option
                      v-for="t in ds.tables.filter(t => !tables.find(lt => lt.id === t.id))"
                      :key="t.id"
                      :value="t.id"
                    >
                      {{ t.physical_name }} {{ t.term ? `(${t.term})` : "" }}
                    </option>
                  </optgroup>
                </template>
              </select>
              <!-- Field Selector：跨数据集时字段无法自动推断，提示手动填写 -->
              <div class="mt-2">
                <label
                  class="block text-[10px] uppercase font-bold text-gray-400 mb-1"
                  >关联字段</label
                >
                <select
                  v-model="targetField"
                  @change="applyJoinCondition"
                  class="w-full border border-gray-200 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-purple-400 outline-none bg-gray-50/50"
                >
                  <option value="">-- 选择字段 --</option>
                  <option
                    v-for="col in targetColumns"
                    :key="col.physical_name"
                    :value="col.physical_name"
                  >
                    {{ col.physical_name }}
                    {{ col.term ? `[${col.term}]` : "" }}
                  </option>
                </select>
              </div>
            </div>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1"
              >关联类型</label
            >
            <div class="flex gap-4">
              <label class="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  v-model="form.join_type"
                  value="left"
                  class="text-purple-600 focus:ring-purple-500"
                />
                <span class="text-sm">Left Join (1:N)</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  v-model="form.join_type"
                  value="inner"
                  class="text-purple-600 focus:ring-purple-500"
                />
                <span class="text-sm">Inner Join</span>
              </label>
              <label class="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  v-model="form.join_type"
                  value="one_to_one"
                  class="text-purple-600 focus:ring-purple-500"
                />
                <span class="text-sm">One to One</span>
              </label>
            </div>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1"
              >关联条件 (ON ...)</label
            >
            <input
              v-model="form.join_condition"
              class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-purple-500 focus:outline-none"
              placeholder="e.g. t1.user_id = t2.id"
            />
            <p class="text-xs text-gray-400 mt-1">使用表别名或完整表名均可。</p>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1"
              >描述</label
            >
            <textarea
              v-model="form.description"
              rows="2"
              class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 focus:outline-none"
            ></textarea>
          </div>
        </div>
        <div class="p-6 bg-gray-50 flex justify-end gap-3">
          <button
            @click="showModal = false"
            class="px-4 py-2 border border-gray-300 rounded-lg text-sm bg-white hover:bg-gray-50"
          >
            取消
          </button>
          <button
            @click="handleSave"
            :disabled="saving"
            class="px-6 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm font-bold shadow-md disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </div>
    </div>
    <!-- Delete Modal -->
    <div
      v-if="deleteId"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      @click.self="deleteId = null"
    >
      <div
        class="bg-white rounded-xl shadow-2xl w-full max-w-sm overflow-hidden border border-gray-100 transform transition-all animate-fade-in-up"
      >
        <div class="p-6 text-center">
          <div
            class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-red-100"
          >
            <svg
              class="w-8 h-8 text-red-500"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                stroke-linecap="round"
                stroke-linejoin="round"
                stroke-width="2"
                d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
              />
            </svg>
          </div>
          <h3 class="text-lg font-bold text-gray-900 mb-2">确认删除?</h3>
          <p class="text-sm text-gray-500 mb-6">
            您确定要删除此关联关系吗？<br />此操作无法撤销。
          </p>
          <div class="flex gap-3 justify-center">
            <button
              @click="deleteId = null"
              class="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-50 bg-white"
            >
              取消
            </button>
            <button
              @click="confirmDelete"
              class="px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-lg text-sm font-medium shadow-md transition-colors shadow-red-500/30"
            >
              确认删除
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.animate-fade-in-up {
  animation: fadeInUp 0.3s ease-out;
}
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
