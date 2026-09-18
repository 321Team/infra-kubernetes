# 리뷰·병합과 Lead 판단

적용 기준: 2026-09-18 KST [병합된 중앙 리뷰 권한 정책](https://github.com/321Team/fds-project-baseline/blob/b2e4612cf2e417b10e7f145c5b1e5f58dbe1b948/governance/REVIEW_AUTHORITY_POLICY.md). 사람 간 승인 절차를 정렬하며 요구사항·AC·업무 담당·보안 통제를 바꾸지 않는다. 과거 실행·승인·불변 소스 기록은 작성 당시 이력으로 보존한다.

## 변경 분류

| 변경 | 검토와 병합 |
|---|---|
| 코드·실행 설정·혼합 PR | 작성자 외 적격 검토자 최소 1인의 정확한 HEAD 독립 리뷰 후 Lead가 병합 판단 |
| 이 저장소의 문서·증적만 변경 | 책임 Lead의 검토·병합 판단. 작성자와 Lead가 같아도 자기 GitHub APPROVE는 요구하지 않음 |
| fds-project-evidence의 모든 PR | 코드 포함 여부와 무관하게 독립 리뷰 유지. 이 저장소 문서 절차를 적용해 생략하지 않음 |

코드에는 앱·테스트·스크립트·IaC/Ansible·manifest·Workflow·의존성·인증서 profile·실행 시 소비되는 정책/설정이 포함된다. 제목이나 확장자 대신 실제 diff와 사용처로 판단한다. 실행 결과와 당시 사용 소스의 보존 사본은 새 실행 코드 변경과 구분한다. 기록 경로를 새 실행 입력으로 소비하도록 바꾸면 코드 변경이다.

## PR와 확인

1. 작업 브랜치에서 PR을 만들고 관련 티켓/AC, 변경·영향·복구, 전체 HEAD SHA, 검증 결과와 제한을 기록한다. main 직접 push·force push·자동 병합을 하지 않는다.
2. 코드/혼합 PR은 작성자가 아닌 기존 역할 범위의 적격 검토자가 검토한다. Reviewer 지정, 기술 의견, 실제 GitHub APPROVED를 구분한다. AI 자체 점검이나 작성자의 자체 검토는 독립 승인이 아니다.
3. 새 commit의 변경 부분과 영향 범위만 재검토한다. 병합 담당자는 최종 HEAD, 필요한 독립 검토 근거, 해당 단계 필수 검사와 미해결 차단사항을 확인하여 수동 병합한다. 여러 단계 판단은 한 PR/티켓 기록에 함께 남길 수 있다.
4. 역할은 책임 티켓의 기존 Lead·Support 및 저장소 병합 담당을 유지한다. 이 문서는 GitHub 계정·팀·서버 권한이나 branch protection/ruleset을 변경하지 않는다.

## 실행·인수·종결

해당 책임 Lead가 문서·계획, 운영 실행·배포·영구 반영·복구, 결과 수용·AC 인수·티켓 종결을 판단한다. 본인이 수행한 업무도 필수 AC와 실제 증적으로 인수할 수 있다. 동일 코드의 각 단계에서 Support/독립 Reviewer의 반복 승인을 일률적 필수 조건으로 두지 않는다. 새 코드/실행 설정 변경은 다시 코드 PR 절차를 따른다.

운영 실행은 검토·병합된 SHA/Digest, 대상·계정·작업 경로·선행조건, 성공/중단 기준과 복구안을 고정한다. 후보 SHA의 정적·모의·CI·격리 시험은 범위와 격리/복구 조건을 기록해 Lead 판단으로 진행할 수 있다. 후보 시험이나 코드 병합을 운영 반영 완료로 표시하지 않는다.

Lead는 기존 기록에 대상/단계·Lead·KST 시각·해당 SHA/Digest·검증/원본 증적·판정/잔여·해당 복구 방법을 남긴다. 비해당 항목의 별도 서류를 만들 필요는 없다. STATIC/MOCK/CI/RUNTIME/REVIEW/MERGE/PUBLICATION은 구분한다. 해시 일치는 무결성이고 CI 0건은 NOT RUN이다.

필수 AC·시험·보안·실패 시 중단·회수 조건은 유지한다. 비밀키·Token·Password·Secret은 커밋하지 않는다. 원본 증적·과거 승인 이력과 진행 중인 실행의 기존 계약을 소급 수정하지 않는다. 증적 저장소 편입 PR은 독립 리뷰를 거치며, 그 병합만으로 업무 티켓을 자동 종결하지 않는다.

## Kubernetes의 현재 계약과 전환

업무 Lead는 책임 티켓을 따른다. P1-K8S-01은 이하영, ICR-D-NET-01-01은 이권욱이다. ICR의 Lead 인수를 Kubernetes 전체 인수나 타 업무 권한으로 확대하지 않는다.

이번 코드 변경은 main의 Node firewall controller가 명시적인 ICR Lead 실행 결정(v3)과 CP1 Lead 인수(v2)를 받을 수 있게 한다. 기존 v2 실행·v1 독립 인수도 원래 계약으로 검증한다. 새 형식으로 과거 승인자·receipt·타이머를 덮어쓰지 않는다. 자세한 형식과 한계는 [Node 권한 모델](runbooks/NODE_AUTHORIZATION_MODEL.md)을 따른다.

cp-canary-node.py는 승인 producer의 byte/hash를 유지한다. 그 실행 입력의 독립 리뷰 참조는 같은 source SHA에 대한 코드 승인 근거이며, 동일 코드의 매 실행마다 새 독립 리뷰를 생성하라는 뜻이 아니다. Lead의 실행 결정과 필수 보호·복구·작업창은 별도로 확인한다.

열린 #11/#14/#15/#16의 최소 CNI 변경·세션·Join 경로와 이미 발급된 Job/receipt는 각 고정 소스의 계약을 유지한다. 이번 main용 controller에 해당 경로의 다른 schema나 이미 가입한 topology를 넣어서는 안 된다. 해당 PR을 main에 통합할 때 새 Lead 인수 schema의 소비 연결과 자체 source manifest를 검토해야 한다. 현재 세션의 PENDING을 ACCEPTED로 일괄 치환하거나 새 코드로 기존 Job을 인수하지 않는다.
