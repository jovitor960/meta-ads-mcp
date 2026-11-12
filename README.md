# Meta Ads MCP

Servidor MCP 0.2.1 com OAuth da Meta, autenticação individual, auditoria e
permissões `read`/`write` nos escopos `all`, `business` ou `account`.

## Segurança das escritas

- campanhas, conjuntos e anúncios são sempre criados como `PAUSED`;
- atualizações recusam conjuntos e anúncios que não estejam `PAUSED`;
- não existem ferramentas para ativar ou excluir recursos;
- orçamentos respeitam `META_ADS_MAX_BUDGET_MINOR`;
- cada operação exige permissão `write` e gera auditoria.
- pixels/datasets podem ser listados, inspecionados, criados e vinculados;
- eventos de conversão só podem ser enviados com `test_event_code` obrigatório.

## Rotas

- MCP: `/mcp`
- OAuth: `/oauth/meta/start` e `/oauth/meta/callback`
- saúde: `/health`

## Destino WhatsApp nos conjuntos

Para `destination_type="WHATSAPP"`, informe `promoted_object.page_id` e pelo menos
um seletor explícito: `whatsapp_phone_number` (telefone internacional com DDI) ou
`whats_app_business_phone_number_id` (ID real do ativo na Meta). Ambos podem ser
informados quando correspondem ao mesmo número.

O telefone aceita `+`, espaços, parênteses e hífens; é enviado somente com os
dígitos internacionais no campo `whatsapp_phone_number`. Ele nunca é convertido
em um ID. O alias antigo `whatsapp_phone_number_id` continua aceito e é enviado
como `whats_app_business_phone_number_id`; IDs conflitantes são recusados.

Os IDs são validados quanto ao formato. A existência dos ativos, o vínculo com a
Página e as permissões dependem da Meta. `validate_paused_adset` verifica as
regras locais e mostra a requisição, sem criar recursos nem simular sua aceitação
pela API da Meta. A criação mantém o conjunto como `PAUSED`.

## Identidade Instagram nos criativos

Use `creative_spec.instagram_user_id` para informar a identidade Instagram. O
MCP envia esse valor como `object_story_spec.instagram_user_id`, conforme a API
da Meta v26. O campo antigo `instagram_actor_id` continua aceito como alias na
entrada, mas nunca é enviado à Meta. Se ambos forem informados, os IDs devem
ser iguais. A regra se aplica à criação, atualização e validação dos anúncios.

O ID precisa estar acessível à conta de anúncios; a tradução do campo não
altera vínculos nem permissões dos ativos na Meta.

## Desenvolvimento

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\pytest.exe
```

## Billing and prepaid funds (0.2.1)

`get_account_billing(account_id)` uses the existing account read authorization and
success audit. It returns prepaid status, currency, raw balance and spending,
spend cap, funding-source details, user tasks, field availability, and UTC query time.
Optional fields are queried separately so their failure preserves basic details.

For prepaid BRL accounts, `available_funds` is a decimal string in BRL major units
parsed only from Meta's explicit Portuguese `Saldo disponivel` label (with accent)
in `funding_source_details.display_string`. The response retains the original text,
source, and status `reported_by_meta`. This format was observed against a live
prepaid account; it depends on Meta's language/format and is not a dedicated numeric
API field. Other formats/currencies return null and `unavailable`, never zero.
Postpaid accounts return `not_applicable`. Balance, spending caps and coupons are
never used to infer available funds. Raw monetary fields retain Meta's units.

The tool does not require enabling mutations or changing OAuth scopes. Access to
optional financial fields still depends on Meta account permissions.

## Image carousels (0.2.1)

Creation, update and validation accept `creative_spec.cards` with 2 to 10 ordered
image cards. Each card requires `link_url` and exactly one of `image_hash` or
`image_url`; optional fields are `headline`, `description`, `call_to_action_type`.
A card inherits the creative CTA when omitted. Upload images with `upload_ad_image`
first to obtain reusable hashes, or supply public image URLs. Do not combine cards
with a top-level image. Existing single-image and creative_id inputs still work.

Example creative_spec:

```json
{
  "page_id": "123456789",
  "link_url": "https://example.com/",
  "message": "Explore our products",
  "call_to_action_type": "LEARN_MORE",
  "cards": [
    {"link_url": "https://example.com/one", "image_hash": "HASH_ONE", "headline": "First product"},
    {"link_url": "https://example.com/two", "image_hash": "HASH_TWO", "headline": "Second product"}
  ]
}
```

The adapter sends `link_data.child_attachments`, disables automatic card reordering
and the automatic end card, and preserves existing Page/Instagram identity behavior.
Ads remain PAUSED and all account, permission and parent-state checks still apply.
This release supports image cards only. Meta still decides whether the creative is
eligible for the chosen objective, placement and destination; local validation
reports the payload and does not prove acceptance by Meta.

Field reference: https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/adcreativelinkdatachildattachment.py


## Paused children under active parents (0.2.1)

Creating and validating new ad sets accepts parent campaigns with status ACTIVE or
PAUSED. Creating and validating new ads accepts both campaign and ad set parents
with status ACTIVE or PAUSED. New children are always sent with status PAUSED;
parent resources are never modified. Missing, archived, deleted or other parent
statuses are rejected locally. Account ownership, authorization, budget and
objective checks remain enforced. Updates still require the target campaign,
ad set or ad itself to already be PAUSED. There is no activation tool.
