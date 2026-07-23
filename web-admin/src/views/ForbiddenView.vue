<template>
  <el-result icon="warning" title="无访问权限" sub-title="请联系管理员为你开通后台角色。">
    <template #extra>
      <el-button type="primary" @click="$router.push('/')">返回首页</el-button>
    </template>
  </el-result>
</template>
