--[[
  Dragonwilds Toolkit - live cheats (UE4SS Lua mod)

  Turns on the game's own developer cheat menu (DominionCheatManager) and runs
  commands the editor's Cheats tab writes to commands.txt. Files live in this
  mod's folder:
    commands.txt   editor -> game   one command per line: seq<TAB>function<TAB>arg...
    results.txt    game -> editor   seq<TAB>ok|error<TAB>message
    functions.txt  game -> editor   every cheat function and its parameters
    status.txt     game -> editor   heartbeat: time, player, cheat menu ready
]]

local UEHelpers = require("UEHelpers")

local DIR = "ue4ss/Mods/DWEditorCheats/"
-- Raise when this script changes. The toolkit compares it with the script it ships and asks
-- you to restart the game when the running one is older (the game reads it only at start).
local MOD_VERSION = 8
local CHEAT_CLASSES = {
    "/Script/Dominion.DominionCheatManager",
    "/Script/Engine.CheatManager",
}

local lastSeq = -1
local dumped = false

local function log(msg)
    print("[DWEditorCheats] " .. tostring(msg) .. "\n")
end

local function append(file, line)
    local f = io.open(DIR .. file, "a")
    if f then
        f:write(line .. "\n")
        f:close()
    end
end

local function write(file, text)
    local f = io.open(DIR .. file, "w")
    if f then
        f:write(text)
        f:close()
    end
end

local function split(line, sep)
    local out = {}
    for part in string.gmatch(line .. sep, "(.-)" .. sep) do
        table.insert(out, part)
    end
    return out
end

-- Your own player controller. The stock UEHelpers.GetPlayerController() returns the first
-- controller it finds, and when you host, your friends' controllers are on this PC too.
local function MyController()
    local first = nil
    for _, pc in ipairs(FindAllOf("PlayerController") or {}) do
        if pc:IsValid() and not pc:GetFName():ToString():find("^Default__") then
            local ok, isLocal = pcall(function() return pc:IsLocalController() end)
            if ok and isLocal then return pc end
            if not first then first = pc end
        end
    end
    return first or UEHelpers.GetPlayerController()
end

-- The cheat menu object; created on the player controller when the game didn't make one.
local function GetCheatManager()
    local pc = MyController()
    if not pc or not pc:IsValid() then
        return nil, "no player yet (load into a world)"
    end
    local cm = pc.CheatManager
    if cm and cm:IsValid() then
        return cm
    end
    local cls = pc.CheatClass
    if not cls or not cls:IsValid() then
        cls = StaticFindObject(CHEAT_CLASSES[1])
    end
    if not cls or not cls:IsValid() then
        cls = StaticFindObject(CHEAT_CLASSES[2])
    end
    if not cls or not cls:IsValid() then
        return nil, "cheat menu class not found"
    end
    cm = StaticConstructObject(cls, pc)
    if not cm or not cm:IsValid() then
        return nil, "could not create the cheat menu"
    end
    pc.CheatManager = cm
    pcall(function() cm:ReceiveInitCheatManager() end)
    log("cheat menu enabled")
    return cm
end

-- functions.txt: name<TAB>class<TAB>param:type,param:type
local function DumpFunctions()
    local lines = {}
    for _, path in ipairs(CHEAT_CLASSES) do
        local cls = StaticFindObject(path)
        if cls and cls:IsValid() then
            local short = path:match("%.(.+)$")
            pcall(function()
                cls:ForEachFunction(function(fn)
                    local name = fn:GetFName():ToString()
                    local params = {}
                    pcall(function()
                        fn:ForEachProperty(function(prop)
                            local pname = prop:GetFName():ToString()
                            local ptype = prop:GetClass():GetFName():ToString()
                            if pname ~= "ReturnValue" then
                                table.insert(params, pname .. ":" .. ptype)
                            end
                        end)
                    end)
                    table.insert(lines, name .. "\t" .. short .. "\t" .. table.concat(params, ","))
                end)
            end)
        end
    end
    if #lines > 0 then
        write("functions.txt", table.concat(lines, "\n") .. "\n")
        dumped = true
        log("listed " .. #lines .. " cheat functions")
    end
end

local PARAM_TYPES = {}

local function ParamTypes(fnName)
    if PARAM_TYPES[fnName] then
        return PARAM_TYPES[fnName]
    end
    local types = {}
    local f = io.open(DIR .. "functions.txt", "r")
    if f then
        for line in f:lines() do
            local parts = split(line, "\t")
            if parts[1] == fnName and parts[3] and parts[3] ~= "" then
                for _, p in ipairs(split(parts[3], ",")) do
                    local t = p:match(":(.+)$")
                    table.insert(types, t)
                end
            end
        end
        f:close()
    end
    PARAM_TYPES[fnName] = types
    return types
end

local function Convert(value, ptype)
    if ptype == "BoolProperty" then
        return value == "true" or value == "1"
    elseif ptype == "FloatProperty" or ptype == "DoubleProperty" or ptype == "IntProperty"
        or ptype == "Int64Property" or ptype == "ByteProperty" or ptype == "UInt32Property" then
        return tonumber(value) or 0
    elseif ptype == "NameProperty" then
        return FName(value)
    elseif ptype == "TextProperty" then
        return FText(value)
    end
    return value -- StrProperty and anything else: plain string
end

-- ---------------------------------------------------------------- inspection
local SIMPLE = { BoolProperty = true, FloatProperty = true, DoubleProperty = true, IntProperty = true,
                 Int64Property = true, ByteProperty = true, UInt32Property = true, NameProperty = true,
                 StrProperty = true, EnumProperty = true }

-- Every property of obj (whole class chain) as lines: label, class, name, type, value.
local function DumpProps(obj, label, out)
    local ok = pcall(function()
        local cls = obj:GetClass()
        while cls and cls:IsValid() do
            local cname = cls:GetFName():ToString()
            cls:ForEachProperty(function(prop)
                local n = prop:GetFName():ToString()
                local t = prop:GetClass():GetFName():ToString()
                local v = ""
                if SIMPLE[t] then
                    local okv, val = pcall(function() return obj[n] end)
                    if okv then
                        if t == "NameProperty" then pcall(function() val = val:ToString() end) end
                        if t == "StrProperty" then pcall(function() val = val:ToString() end) end
                        v = tostring(val)
                    end
                elseif t == "StructProperty" then
                    pcall(function() v = "struct " .. prop:GetStruct():GetFName():ToString() end)
                    pcall(function()
                        local s = obj[n]
                        local cur = s.CurrentValue
                        if cur ~= nil then v = v .. " Current=" .. tostring(cur) .. " Base=" .. tostring(s.BaseValue) end
                    end)
                elseif t == "ObjectProperty" then
                    pcall(function()
                        local o = obj[n]
                        if o and o:IsValid() then v = "-> " .. o:GetClass():GetFName():ToString() end
                    end)
                end
                table.insert(out, label .. "\t" .. cname .. "\t" .. n .. "\t" .. t .. "\t" .. v)
            end)
            cls = cls:GetSuperStruct()
        end
    end)
    if not ok then table.insert(out, label .. "\t?\t(could not read)") end
end

local function Components(actor)
    local list = {}
    pcall(function()
        local arr = actor:K2_GetComponentsByClass(StaticFindObject("/Script/Engine.ActorComponent"))
        arr:ForEach(function(_, elem)
            local c = elem:get()
            if c and c:IsValid() then table.insert(list, c) end
        end)
    end)
    return list
end

local function Inspect()
    local out = {}
    local pc = MyController()
    if not pc or not pc:IsValid() then return "no player" end
    local pawn = pc.Pawn
    local targets = { { "Controller", pc } }
    if pawn and pawn:IsValid() then table.insert(targets, { "Pawn", pawn }) end
    local ps = pc.PlayerState
    if ps and ps:IsValid() then table.insert(targets, { "PlayerState", ps }) end
    for _, t in ipairs(targets) do
        DumpProps(t[2], t[1], out)
        for _, c in ipairs(Components(t[2])) do
            DumpProps(c, t[1] .. "." .. c:GetFName():ToString(), out)
            -- attribute sets live inside the ability system component
            pcall(function()
                c.SpawnedAttributes:ForEach(function(_, e)
                    local a = e:get()
                    if a and a:IsValid() then DumpProps(a, t[1] .. ".Attr." .. a:GetFName():ToString(), out) end
                end)
            end)
        end
    end
    write("inspect.txt", table.concat(out, "\n") .. "\n")
    return #out .. " values written to inspect.txt"
end

-- Developer channel: runs one line of Lua sent by the editor, only when dev.flag exists.
local function DevLua(code)
    local f = io.open(DIR .. "dev.flag", "r")
    if not f then return "developer channel is off" end
    f:close()
    local chunk, err = load(code)
    if not chunk then return "compile error: " .. tostring(err) end
    local ok, res = pcall(chunk)
    return (ok and "ok: " or "error: ") .. tostring(res)
end

-- ------------------------------------------------------------- live cheats
-- Switch-type cheats come from wanted.txt (key=value lines) and are kept applied.
-- They work by changing the game's own live values (tested in game 2026-10-15).

local Defaults = { attr = {}, move = nil, lossRate = nil }

-- The game's own values, kept in defaults.txt so a mod reload can never mistake
-- a cheated value for the normal one. Only ever filled in, never overwritten.
-- A "normal" speed or jump of (almost) zero is never real: on a friend's PC (not the host)
-- the stats can read 0 before the server has sent them. Recording that froze their walk animation.
local function BadNormal(k, v)
    return (k == "walk" or k == "jump" or k:match("Speed$")) and v < 1
end

local function LoadDefaults()
    local f = io.open(DIR .. "defaults.txt", "r")
    if not f then return end
    for line in f:lines() do
        local k, v = line:match("^([%w_]+)=([%d%.%-]+)$")
        v = tonumber(v)
        if k and v and not BadNormal(k, v) then
            if k == "walk" or k == "jump" then
                Defaults.move = Defaults.move or {}
                Defaults.move[k] = v
            elseif k == "lossRate" then
                Defaults.lossRate = v
            else
                Defaults.attr[k] = v
            end
        end
    end
    f:close()
end

local function SaveDefault(k, v)
    local f = io.open(DIR .. "defaults.txt", "a")
    if f then
        f:write(k .. "=" .. tostring(v) .. "\n")
        f:close()
    end
end

LoadDefaults()
Defaults.save = SaveDefault

local function Pawn()
    local pc = MyController()
    if not pc or not pc:IsValid() then return nil, nil end
    local p = pc.Pawn
    if not p or not p:IsValid() then return pc, nil end
    return pc, p
end

local function AttrIndex(ac)
    local idx = {}
    ac.FloatAttributes:ForEach(function(i, e)
        local a = e:get()
        if a and a:IsValid() then idx[a:GetClass():GetFName():ToString()] = { i, a } end
    end)
    return idx
end

-- Set a player stat (base value + live value), remembering the game's own value first.
-- D is where normal values are kept: Defaults for you, one table per other player.
local function SetAttr(ac, idx, name, value, D)
    D = D or Defaults
    local entry = idx[name]
    if not entry then return end
    local i, a = entry[1], entry[2]
    if D.attr[name] == nil then
        D.attr[name] = a.BaseValue
        D.save(name, a.BaseValue)
    end
    if value == nil then value = D.attr[name] end
    if math.abs(ac.AttributeValues[i] - value) > 0.0001 or math.abs(a.BaseValue - value) > 0.0001 then
        a.BaseValue = value
        ac.AttributeValues[i] = value
    end
end

-- Shared stats (health, max health, run/sprint/mount speeds) live in a second list.
local function SharedIndex(ac)
    local idx = {}
    ac.SharedFloatAttributes:ForEach(function(i, e)
        local a = e:get()
        if a and a:IsValid() then idx[a:GetClass():GetFName():ToString()] = { i, a } end
    end)
    return idx
end

local function SharedGet(ac, idx, name)
    local entry = idx[name]
    return entry and ac.SharedAttributeValues[entry[1]] or nil
end

-- value nil = back to the game's own value (remembered as "S_<name>").
local function SetShared(ac, idx, name, value, D)
    D = D or Defaults
    local entry = idx[name]
    if not entry then return false end
    local i, a = entry[1], entry[2]
    local key = "S_" .. name
    if D.attr[key] == nil then
        local normal = a.BaseValue
        if BadNormal(key, normal) then normal = ac.SharedAttributeValues[i] end
        if BadNormal(key, normal) then return false end -- not known yet; touch nothing
        D.attr[key] = normal
        D.save(key, normal)
    end
    if value == nil then value = D.attr[key] end
    if math.abs(ac.SharedAttributeValues[i] - value) > 0.0001 or math.abs(a.BaseValue - value) > 0.0001 then
        a.BaseValue = value
        ac.SharedAttributeValues[i] = value
        return true
    end
    return false
end

-- Tell the HUD that health changed (it only refreshes on a health event).
local function NudgeHealth(hc)
    pcall(function()
        hc:DecreaseHealth(1.0, "DWEditorCheats")
        hc:IncreaseHealth(1.0, "DWEditorCheats")
    end)
end

local GOD_HEALTH = 999.0
local SPEEDS = { "RunSpeed", "SprintSpeed", "MountRunSpeed", "MountSprintSpeed" }
local FlyLandUntil = 0

local function ReadWanted()
    local w = {}
    local f = io.open(DIR .. "wanted.txt", "r")
    if f then
        for line in f:lines() do
            local k, v = line:match("^%s*([%w_]+)%s*=%s*(.-)%s*$")
            if k and v ~= "" then w[k] = tonumber(v) or v end
        end
        f:close()
    end
    return w
end

local ATTRS = {
    carry = { "MaxCarryWeightAttribute" },
    xp = { "XpModifierAttribute" },
    -- damage you deal (melee, ranged and magic). DamageModifierAttribute is damage you take.
    damage = { "DamageMultiplierAllCombatStylesAttribute" },
    falldamage = { "FallDamageMultiplier" },
    nohunger = { "SustenanceDecayMultiplier", "HydrationDecayMultiplier", "EnduranceDecayMultiplier",
                 "EnduranceReductionRatePerInGameHour" },
}

-- No hunger: food and water use their own hourly decay rate (the multipliers above don't stop
-- them), so set those rates to 0 and keep all three meters full. D keeps the normal rates.
local function HungerLock(p, on, D)
    local s, h, e = p.SustenanceComponent, p.HydrationComponent, p.EnduranceComponent
    if on then
        if D.attr.sustRate == nil and s.DecayRatePerHour > 0 then
            D.attr.sustRate = s.DecayRatePerHour
            D.save("sustRate", s.DecayRatePerHour)
        end
        if D.attr.hydRate == nil and h.DecayRatePerHour > 0 then
            D.attr.hydRate = h.DecayRatePerHour
            D.save("hydRate", h.DecayRatePerHour)
        end
        if s.DecayRatePerHour ~= 0 then s.DecayRatePerHour = 0 end
        if h.DecayRatePerHour ~= 0 then h.DecayRatePerHour = 0 end
        local ms = s.CurrentMaxSustenance - s.CurrentSustenance
        if ms > 0.5 then s:AddSustenance(ms, 0) end
        local mh = h.CurrentMaxHydration - h.CurrentHydration
        if mh > 0.5 then h:AddHydration(mh, 0) end
        local me = e.MaxEndurance - e.CurrentEndurance
        if me > 0.5 then e:AddEndurance(me, 0) end
    else
        if D.attr.sustRate then s.DecayRatePerHour = D.attr.sustRate end
        if D.attr.hydRate then h.DecayRatePerHour = D.attr.hydRate end
    end
end

-- Versions before MOD_VERSION 5 changed DamageModifierAttribute (damage taken) for "damage";
-- put it back once if one of them left it changed.
-- ---------------------------------------------------------------- god mode
-- One switch (wanted key "godmode") for everything: it turns on these normal switches...
local GOD_SWITCHES = { god = 1, durability = 1, nohunger = 1, carry = 999999, damage = 100, walk = 3, xp = 10,
                       falldamage = 0 }
-- ...and sets these stats while it's on (put back to normal when it goes off).
local GOD_ATTRS = {
    StaminaCostMultiplier = 0, MeleeAttackStaminaCostModifierAttribute = 0, RangedAttackStaminaCostModifierAttribute = 0,
    MagicAttackStaminaCostModifierAttribute = 0, BlockStaminaCostModifierAttribute = 0, JumpStaminaCost = 0,
    EvadeStaminaCost = 0, DoubleJumpStaminaCost = 0,
    CriticalHitChanceBaseAttribute = 1, MaxCriticalHitChanceAttribute = 1,
    MagicAttackRuneCostMultiplierAttribute = 0, RangedAttackProjectileRefundChanceAttribute = 1,
    NoCostBuildingChance = 1, ArtisanCraftingNoCostChanceAttribute = 1, ConstructionNoCostRepairBuildingAttribute = 1,
    ArtisanRepairCostRefundChanceAttribute = 1, RunecraftingRepairCostRefundChanceAttribute = 1,
    MiningDropAmountAttribute = 2, WoodcuttingDropAmountAttribute = 2,

    DurabilityLossModifierAttribute = 0,
}
-- Status build-ups (poison, bleeding, burning, cold...) are held at 0; they're never "restored".
local GOD_CLEAR = { "PoisonBuildUp", "BleedingBuildUp", "ColdBuildUp", "BurningBuildUp", "WitherBuildUp",
                    "ScorchBuildup", "SoulScourgeBuildUp", "ShockedBuildUp" }

-- The switches as they apply: with god mode on, its values replace the single switches.
local function GodExpand(w)
    if w.godmode ~= 1 then return w end
    local e = {}
    for k, v in pairs(w) do e[k] = v end
    for k, v in pairs(GOD_SWITCHES) do e[k] = v end
    return e
end

-- Extra god-mode stats for one character; D keeps that character's normal values.
local function ApplyGodAttrs(ac, on, wasOn, D)
    if not on and not wasOn then return end
    local idx = AttrIndex(ac)
    for name, v in pairs(GOD_ATTRS) do
        if on then pcall(SetAttr, ac, idx, name, v, D) elseif D.attr[name] ~= nil then pcall(SetAttr, ac, idx, name, nil, D) end
    end
    if on then
        for _, name in ipairs(GOD_CLEAR) do
            local entry = idx[name]
            if entry and ac.AttributeValues[entry[1]] ~= 0 then
                entry[2].BaseValue = 0
                ac.AttributeValues[entry[1]] = 0
            end
        end
        -- special attack always charged: the game spends and reads the base value, so fill both
        local sp, mx = idx["SpecialChargeAttribute"], idx["MaxSpecialChargeAttribute"]
        if sp and mx then
            local full = ac.AttributeValues[mx[1]]
            if sp[2].BaseValue < full or ac.AttributeValues[sp[1]] < full then
                sp[2].BaseValue = full
                ac.AttributeValues[sp[1]] = full
                -- the HUD bar only redraws when the game announces a change
                pcall(function() ac:OnAttributeChanged(sp[2]) end)
            end
        end
    end
end

local function LegacyDamageFix(ac, idx, D)
    if D.legacyDamageDone then return end
    D.legacyDamageDone = true
    if D.attr["DamageModifierAttribute"] ~= nil then SetAttr(ac, idx, "DamageModifierAttribute", nil, D) end
end

-- ------------------------------------------------------------------ overlay
-- A see-through bar along the top of the screen listing every numpad key and its state,
-- drawn by the game's own UI system.
-- Shown unless wanted.txt has hideoverlay=1 (Num * or the editor's switch).
local Overlay = { widget = nil, texts = {}, last = {} }
-- One entry per numpad key: label, wanted key (nil = one-shot action), shown value
local BAR = {
    { "1 GOD MODE", "godmode" }, { "2 Carry 99999", "carry" }, { "3 Refill" }, { "4 Heal" },
    { "5 Durability", "durability" }, { "6 No hunger", "nohunger" }, { "7 Speed", "walk", 2 },
    { "8 Damage", "damage", 10 }, { "9 Gravestone" }, { ". Back" }, { "0 Normal" }, { "+ Fly", "fly" },
    { "- Home" }, { "/ Set home" }, { "* Hide bar" },
}
local GREEN = { SpecifiedColor = { R = 0.5, G = 1.0, B = 0.45, A = 1.0 }, ColorUseRule = 0 }
local GREY = { SpecifiedColor = { R = 0.7, G = 0.7, B = 0.7, A = 1.0 }, ColorUseRule = 0 }
local WHITE = { SpecifiedColor = { R = 1.0, G = 1.0, B = 1.0, A = 1.0 }, ColorUseRule = 0 }

local function New(cls, owner, name)
    return StaticConstructObject(StaticFindObject(cls), owner, FName(name))
end

-- Full-width see-through bar stuck to the top of the screen; never takes mouse clicks.
local function EnsureOverlay()
    local w = Overlay.widget
    if w and w:IsValid() and w:IsInViewport() then return w end
    w = New("/Script/UMG.UserWidget", UEHelpers.GetGameInstance(), "DWEditorBar")
    w.WidgetTree = New("/Script/UMG.WidgetTree", w, "DWEditorBarTree")
    local canvas = New("/Script/UMG.CanvasPanel", w.WidgetTree, "DWEditorBarCanvas")
    w.WidgetTree.RootWidget = canvas
    local border = New("/Script/UMG.Border", w.WidgetTree, "DWEditorBarBack")
    border:SetBrushColor({ R = 0.0, G = 0.0, B = 0.0, A = 0.55 })
    border:SetPadding({ Left = 10.0, Top = 2.0, Right = 10.0, Bottom = 2.0 })
    local slot = canvas:AddChildToCanvas(border)
    slot:SetAnchors({ Minimum = { X = 0.0, Y = 0.0 }, Maximum = { X = 1.0, Y = 0.0 } })
    slot:SetOffsets({ Left = 0.0, Top = 0.0, Right = 0.0, Bottom = 20.0 })
    local box = New("/Script/UMG.HorizontalBox", w.WidgetTree, "DWEditorBarRow")
    border:SetContent(box)
    Overlay.texts, Overlay.last = {}, {}
    for i = 1, #BAR do
        local t = New("/Script/UMG.TextBlock", w.WidgetTree, "DWEditorBarText" .. i)
        local font = t.Font
        font.Size = 11
        t:SetFont(font)
        box:AddChildToHorizontalBox(t)
        Overlay.texts[i] = t
    end
    w:AddToViewport(999)
    w:SetVisibility(3)
    Overlay.widget = w
    return w
end

local function UpdateOverlay(w)
    local ok = pcall(function()
        if w.hideoverlay == 1 then
            if Overlay.widget and Overlay.widget:IsValid() then Overlay.widget:SetVisibility(2) end
            return
        end
        EnsureOverlay():SetVisibility(3)
        for i, b in ipairs(BAR) do
            local label, key = b[1], b[2]
            local text, color
            if key then
                local v = w[key]
                local shown = b[3] and (" x" .. tostring(v or b[3])) or ""
                text = label .. shown .. (v ~= nil and " ON" or " off")
                color = (v ~= nil) and GREEN or GREY
            else
                text, color = label, WHITE
            end
            text = text .. "    "
            if Overlay.last[i] ~= text then
                Overlay.texts[i]:SetText(FText(text))
                Overlay.texts[i]:SetColorAndOpacity(color)
                Overlay.last[i] = text
            end
        end
    end)
    if not ok then Overlay.widget = nil end
end
local function ApplyWanted()
    local pc, p = Pawn()
    if not p then return end
    local w = GodExpand(ReadWanted())
    UpdateOverlay(w)

    local ac = p.PlayerEncumbranceComponent.AttributesComponent
    local sidx = SharedIndex(ac)

    if w.god == 1 then
        pcall(function()
            local hc = p.HealthComponent
            if not Defaults.god then
                -- remember today's real maximum (it grows with shrines) before raising it
                local maxNow = SharedGet(ac, sidx, "MaxHealthAttribute")
                if maxNow and maxNow < GOD_HEALTH - 1 then
                    Defaults.attr["S_MaxHealthAttribute"] = maxNow
                    SaveDefault("S_MaxHealthAttribute", maxNow)
                end
            end
            hc:SetCanDie(false)
            local changed = SetShared(ac, sidx, "MaxHealthAttribute", GOD_HEALTH)
            local missing = hc:GetMaxHealth() - hc:GetAuthoritativeHealth()
            if missing > 0.5 then hc:IncreaseHealth(missing, "DWEditorCheats") end
            if changed then NudgeHealth(hc) end
        end)
    elseif Defaults.god then
        pcall(function()
            local hc = p.HealthComponent
            hc:SetCanDie(true)
            SetShared(ac, sidx, "MaxHealthAttribute", nil)
            local maxNow = SharedGet(ac, sidx, "MaxHealthAttribute")
            if maxNow and SharedGet(ac, sidx, "HealthAttribute") > maxNow then
                SetShared(ac, sidx, "HealthAttribute", maxNow)
                Defaults.attr["S_HealthAttribute"] = nil
            end
            NudgeHealth(hc)
        end)
    end
    Defaults.god = (w.god == 1)

    -- flying: keep the flying movement mode; on landing, no fall damage for 10 s
    pcall(function()
        local m = p.CharacterMovement
        if w.fly == 1 then
            m.bCheatFlying = true
            m.MaxFlySpeed = 1500.0 * (tonumber(w.walk) or 1)
            if m.MovementMode ~= 5 then m:SetMovementMode(5, 0) end
            Defaults.flying = true
        elseif Defaults.flying then
            Defaults.flying = false
            m.bCheatFlying = false
            m:SetMovementMode(3, 0)
            FlyLandUntil = os.clock() + 10
        end
    end)

    if w.stamina == 1 or w.god == 1 then
        pcall(function()
            local sc = p.StaminaComponent
            local miss = sc:GetMaxStamina() - sc:GetStamina()
            if miss > 0.5 then sc:IncreaseStamina(miss) end
        end)
    end

    pcall(function()
        for _, c in ipairs(FindAllOf("ItemDurabilityComponent") or {}) do
            local o = c:GetOwner()
            if o and o:IsValid() and o:GetAddress() == p:GetAddress() then
                if Defaults.lossRate == nil then
                    Defaults.lossRate = c.DurabilityLossRate
                    SaveDefault("lossRate", c.DurabilityLossRate)
                end
                local want = (w.durability == 1) and 0 or Defaults.lossRate
                if c.DurabilityLossRate ~= want then c.DurabilityLossRate = want end
            end
        end
    end)

    pcall(function()
        local idx = AttrIndex(ac)
        for key, names in pairs(ATTRS) do
            for _, name in ipairs(names) do
                local v = w[key]
                if key == "nohunger" then v = (w.nohunger == 1) and 0.0 or nil end
                if key == "falldamage" and os.clock() < FlyLandUntil then v = 0.0 end
                if v ~= nil or Defaults.attr[name] ~= nil then SetAttr(ac, idx, name, v) end
            end
        end
        LegacyDamageFix(ac, idx, Defaults)
    end)

    pcall(function()
        ApplyGodAttrs(ac, w.godmode == 1, Defaults.godOn, Defaults)
        Defaults.godOn = (w.godmode == 1)
    end)

    pcall(function()
        if w.nohunger == 1 or Defaults.hungerOn then HungerLock(p, w.nohunger == 1, Defaults) end
        Defaults.hungerOn = (w.nohunger == 1)
    end)

    -- speed: walking, running, sprinting and mount speeds move together
    pcall(function()
        local mult = tonumber(w.walk)
        for _, name in ipairs(SPEEDS) do
            local key = "S_" .. name
            if mult then
                if Defaults.attr[key] == nil then SetShared(ac, sidx, name, nil) end
                SetShared(ac, sidx, name, Defaults.attr[key] * mult)
            elseif Defaults.attr[key] ~= nil then
                SetShared(ac, sidx, name, nil)
            end
        end
    end)

    pcall(function()
        local m = p.CharacterMovement
        if not (Defaults.move and Defaults.move.walk and Defaults.move.jump) then
            if m.MaxWalkSpeed < 1 or m.JumpZVelocity < 1 then return end
            Defaults.move = { walk = m.MaxWalkSpeed, jump = m.JumpZVelocity }
            SaveDefault("walk", m.MaxWalkSpeed)
            SaveDefault("jump", m.JumpZVelocity)
        end
        local walk = Defaults.move.walk * (tonumber(w.walk) or 1)
        local jump = Defaults.move.jump * (tonumber(w.jump) or 1)
        if math.abs(m.MaxWalkSpeed - walk) > 0.5 then m.MaxWalkSpeed = walk end
        if math.abs(m.JumpZVelocity - jump) > 0.5 then m.JumpZVelocity = jump end
    end)
end

-- ---------------------------------------------------------------- actions
local Back = nil

local function ItemData(path)
    local d = StaticFindObject(path)
    if not d or not d:IsValid() then d = LoadAsset(path) end
    if not d or not d:IsValid() then error("item not found: " .. path) end
    return d
end

local function Refill(p)
    local s, h, e = p.SustenanceComponent, p.HydrationComponent, p.EnduranceComponent
    s:AddSustenance(s.CurrentMaxSustenance - s.CurrentSustenance, 0)
    h:AddHydration(h.CurrentMaxHydration - h.CurrentHydration, 0)
    e:AddEndurance(e.MaxEndurance - e.CurrentEndurance, 0)
end

local function Heal(p)
    local hc = p.HealthComponent
    local missing = hc:GetMaxHealth() - hc:GetAuthoritativeHealth()
    if missing > 0 then hc:IncreaseHealth(missing, "DWEditorCheats") end
    local sc = p.StaminaComponent
    sc:IncreaseStamina(sc:GetMaxStamina() - sc:GetStamina())
end

local Actions = {}
Actions["dw.refill"] = function()
    local _, p = Pawn()
    Refill(p)
    return "food, water and rest refilled"
end
Actions["dw.heal"] = function()
    local _, p = Pawn()
    Heal(p)
    return "health and stamina full"
end
Actions["dw.revealmap"] = function()
    local pc = Pawn()
    local fog = pc.RevealedMapFogComponent
    local prev = fog.RevealedMapRegionsBitmap
    fog.RevealedMapRegionsBitmap = 4294967295
    pcall(function() fog:OnRep_RevealedMapRegionsBitmap(prev) end)
    return "whole map revealed"
end
Actions["dw.teleport"] = function(args)
    local _, p = Pawn()
    local here = p:K2_GetActorLocation()
    Back = { X = here.X, Y = here.Y, Z = here.Z }
    local z = tonumber(args[3]) or here.Z
    local ok = p:K2_TeleportTo({ X = tonumber(args[1]), Y = tonumber(args[2]), Z = z + 200.0 }, p:K2_GetActorRotation())
    return ok and "teleported" or "teleport refused by the game"
end
Actions["dw.return"] = function()
    if not Back then return "no earlier position" end
    local _, p = Pawn()
    local b = Back
    Back = nil
    p:K2_TeleportTo(b, p:K2_GetActorRotation())
    return "back where you were"
end
-- Home: saved in home.txt as "x,y,z" (set from the editor or with Num /).
local function ReadHome()
    local f = io.open(DIR .. "home.txt", "r")
    if not f then return nil end
    local line = f:read("*l") or ""
    f:close()
    local x, y, z = line:match("^%s*([%-%d%.]+)%s*,%s*([%-%d%.]+)%s*,%s*([%-%d%.]+)")
    if not x then return nil end
    return tonumber(x), tonumber(y), tonumber(z)
end
Actions["dw.sethome"] = function()
    local _, p = Pawn()
    local l = p:K2_GetActorLocation()
    write("home.txt", string.format("%.1f,%.1f,%.1f\n", l.X, l.Y, l.Z))
    return string.format("home set here (%.0f, %.0f)", l.X, l.Y)
end
Actions["dw.home"] = function()
    local x, y, z = ReadHome()
    if not x then return "no home set yet (Num / or the editor)" end
    local _, p = Pawn()
    local here = p:K2_GetActorLocation()
    Back = { X = here.X, Y = here.Y, Z = here.Z }
    p:K2_TeleportTo({ X = x, Y = y, Z = z + 50.0 }, p:K2_GetActorRotation())
    return "teleported home"
end
-- Latest gravestone of this character. The editor writes the character's id to me.txt;
-- the game's registry lists gravestones oldest first.
local function GuidHex(q)
    return string.format("%08X%08X%08X%08X", q.A & 0xFFFFFFFF, q.B & 0xFFFFFFFF, q.C & 0xFFFFFFFF, q.D & 0xFFFFFFFF)
end
Actions["dw.grave"] = function()
    local me = nil
    local f = io.open(DIR .. "me.txt", "r")
    if f then me = (f:read("*l") or ""):upper():gsub("[^0-9A-F]", ""); f:close() end
    local reg = FindFirstOf("GameplayObjectRegistry")
    if not reg or not reg:IsValid() then return "gravestone list not found" end
    local best = nil
    reg.Gravestones:ForEach(function(_, e)
        local g = e:get()
        local owner = nil
        pcall(function() owner = GuidHex(g.OwningCharGuid.InnerGuid) end)
        if not owner then pcall(function() owner = GuidHex(g.OwningCharGuid) end) end
        if not me or me == "" or owner == me then
            best = { X = g.Location.X, Y = g.Location.Y, Z = g.Location.Z }
        end
    end)
    if not best then return "you have no gravestone" end
    local _, p = Pawn()
    local here = p:K2_GetActorLocation()
    Back = { X = here.X, Y = here.Y, Z = here.Z }
    p:K2_TeleportTo({ X = best.X + 150.0, Y = best.Y, Z = best.Z + 200.0 }, p:K2_GetActorRotation())
    return string.format("teleported to your latest gravestone (%.0f, %.0f)", best.X, best.Y)
end
Actions["dw.additem"] = function(args)
    local pc = Pawn()
    local inv = pc.BP_Components_Inventory
    local d = ItemData(args[1])
    local before = inv:GetNumItemsByData(d)
    local ok = inv:AddItemByData(d, tonumber(args[2]) or 1, tonumber(args[3]) or 1.0, {})
    return string.format("%s: %d -> %d", ok and "added" or "no room", before, inv:GetNumItemsByData(d))
end
Actions["dw.setitem"] = function(args)
    local pc = Pawn()
    local inv = pc.BP_Components_Inventory
    local d = ItemData(args[1])
    local want = tonumber(args[2]) or 0
    local have = inv:GetNumItemsByData(d)
    if want > have then inv:AddItemByData(d, want - have, 1.0, {}) end
    if want < have then inv:RemoveItemByData(d, have - want) end
    return string.format("%d -> %d", have, inv:GetNumItemsByData(d))
end

-- ------------------------------------------------------------- other players
-- When you host, the game on this PC is the server: changes made here to another
-- player's character are sent to their game. players.txt lists everyone in the world;
-- pwanted.txt holds switches per player as  name<TAB>key=value  lines (written by the
-- editor); pdefaults.txt remembers each player's normal values (append-only).

local PDefaults = {}

local function PD(name)
    local d = PDefaults[name]
    if not d then
        d = { attr = {}, on = {} }
        d.save = function(k, v) append("pdefaults.txt", name .. "\t" .. k .. "=" .. tostring(v)) end
        PDefaults[name] = d
    end
    return d
end

local function LoadPDefaults()
    local f = io.open(DIR .. "pdefaults.txt", "r")
    if not f then return end
    for line in f:lines() do
        local name, k, v = line:match("^(.-)\t([%w_]+)=([%d%.%-e]+)$")
        v = tonumber(v)
        if name and k and v then
            local d = PD(name)
            if k == "walk" or k == "jump" then
                d.move = d.move or {}
                if d.move[k] == nil then d.move[k] = v end
            elseif k == "lossRate" then
                if d.lossRate == nil then d.lossRate = v end
            elseif d.attr[k] == nil then
                d.attr[k] = v
            end
        end
    end
    f:close()
end
LoadPDefaults()

local function ReadPWanted()
    local all = {}
    local f = io.open(DIR .. "pwanted.txt", "r")
    if f then
        for line in f:lines() do
            local name, k, v = line:match("^(.-)\t([%w_]+)=(.-)%s*$")
            if name and k and v ~= "" then
                all[name] = all[name] or {}
                all[name][k] = tonumber(v) or v
            end
        end
        f:close()
    end
    return all
end

local function IsLocal(pc)
    local ok, v = pcall(function() return pc:IsLocalController() end)
    return ok and v == true
end

local function PlayerName(pc)
    local name = nil
    pcall(function() name = pc.PlayerState:GetPlayerName():ToString() end)
    if not name or name == "" then pcall(function() name = pc.PlayerState.PlayerNamePrivate:ToString() end) end
    if not name or name == "" then name = "Player" end
    return (name:gsub("[\t\r\n]", " "))
end

-- Everyone in the world: { pc, pawn, name, isLocal }
local function AllPlayers()
    local out, seen = {}, {}
    for _, pc in ipairs(FindAllOf("PlayerController") or {}) do
        local ok = pcall(function()
            if not pc:IsValid() or pc:GetFName():ToString():find("^Default__") then return end
            local p = pc.Pawn
            if p and not p:IsValid() then p = nil end
            local name = PlayerName(pc)
            if seen[name] then name = name .. " (2)" end
            seen[name] = true
            table.insert(out, { pc = pc, pawn = p, name = name, isLocal = IsLocal(pc) })
        end)
    end
    return out
end

local function FindPlayer(name)
    for _, pl in ipairs(AllPlayers()) do
        if pl.name == name then
            if not pl.pawn then error(name .. " isn't spawned right now", 0) end
            return pl
        end
    end
    error(name .. " isn't in your world", 0)
end

local function WritePlayers()
    local lines = {}
    for _, pl in ipairs(AllPlayers()) do
        local x, y, z, hp, maxhp = 0, 0, 0, -1, -1
        if pl.pawn then
            pcall(function()
                local l = pl.pawn:K2_GetActorLocation()
                x, y, z = l.X, l.Y, l.Z
            end)
            pcall(function()
                hp = pl.pawn.HealthComponent:GetAuthoritativeHealth()
                maxhp = pl.pawn.HealthComponent:GetMaxHealth()
            end)
        end
        table.insert(lines, string.format("%s\t%d\t%d\t%.1f\t%.1f\t%.1f\t%.0f\t%.0f",
            pl.name, pl.isLocal and 1 or 0, pl.pawn and 1 or 0, x, y, z, hp, maxhp))
    end
    write("players.txt", table.concat(lines, "\n") .. (#lines > 0 and "\n" or ""))
end

-- Keep one other player's switches applied (server side, so it reaches their game).
local function ApplyPlayer(pl, w)
    local p, d = pl.pawn, PD(pl.name)
    local ac = p.PlayerEncumbranceComponent.AttributesComponent
    local sidx = SharedIndex(ac)

    if w.god == 1 then
        pcall(function()
            local hc = p.HealthComponent
            if not d.god then
                local maxNow = SharedGet(ac, sidx, "MaxHealthAttribute")
                if maxNow and maxNow < GOD_HEALTH - 1 then
                    d.attr["S_MaxHealthAttribute"] = maxNow
                    d.save("S_MaxHealthAttribute", maxNow)
                end
            end
            hc:SetCanDie(false)
            local changed = SetShared(ac, sidx, "MaxHealthAttribute", GOD_HEALTH, d)
            local missing = hc:GetMaxHealth() - hc:GetAuthoritativeHealth()
            if missing > 0.5 then hc:IncreaseHealth(missing, "DWEditorCheats") end
            if changed then NudgeHealth(hc) end
            local sc = p.StaminaComponent
            local miss = sc:GetMaxStamina() - sc:GetStamina()
            if miss > 0.5 then sc:IncreaseStamina(miss) end
        end)
    elseif d.god then
        pcall(function()
            local hc = p.HealthComponent
            hc:SetCanDie(true)
            SetShared(ac, sidx, "MaxHealthAttribute", nil, d)
            local maxNow = SharedGet(ac, sidx, "MaxHealthAttribute")
            if maxNow and SharedGet(ac, sidx, "HealthAttribute") > maxNow then
                SetShared(ac, sidx, "HealthAttribute", maxNow, d)
                d.attr["S_HealthAttribute"] = nil
            end
            NudgeHealth(hc)
        end)
    end
    d.god = (w.god == 1)

    pcall(function()
        for _, c in ipairs(FindAllOf("ItemDurabilityComponent") or {}) do
            local o = c:GetOwner()
            if o and o:IsValid() and o:GetAddress() == p:GetAddress() then
                if d.lossRate == nil then
                    d.lossRate = c.DurabilityLossRate
                    d.save("lossRate", c.DurabilityLossRate)
                end
                if w.durability == 1 then
                    if c.DurabilityLossRate ~= 0 then c.DurabilityLossRate = 0 end
                elseif d.on.durability then
                    c.DurabilityLossRate = d.lossRate
                end
            end
        end
        d.on.durability = (w.durability == 1) or nil
    end)

    -- flying (test): the server switches its copy to flying and tells their game to fly too,
    -- with Unreal's own client cheat calls. On landing, no fall damage for 10 seconds.
    pcall(function()
        local m = p.CharacterMovement
        if w.fly == 1 then
            m.bCheatFlying = true
            m.MaxFlySpeed = 1500.0
            if m.MovementMode ~= 5 then
                m:SetMovementMode(5, 0)
                pcall(function() pl.pc:ClientCheatFly() end)
            end
        elseif d.on.fly then
            m.bCheatFlying = false
            m:SetMovementMode(3, 0)
            pcall(function() pl.pc:ClientCheatWalk() end)
            d.landUntil = os.clock() + 10
        end
        d.on.fly = (w.fly == 1) or nil
    end)

    -- stats: set while on; put back once when switched off (so the game's own growth isn't undone)
    pcall(function()
        local idx = AttrIndex(ac)
        for key, names in pairs(ATTRS) do
            local v = w[key]
            if key == "nohunger" then v = (w.nohunger == 1) and 0.0 or nil end
            if key == "falldamage" and os.clock() < (d.landUntil or 0) then v = 0.0 end
            for _, name in ipairs(names) do
                if v ~= nil then
                    SetAttr(ac, idx, name, v, d)
                elseif d.on[key] then
                    SetAttr(ac, idx, name, nil, d)
                end
            end
            d.on[key] = (v ~= nil) or nil
        end
        LegacyDamageFix(ac, idx, d)
    end)

    pcall(function()
        ApplyGodAttrs(ac, w.godmode == 1, d.on.godmode, d)
        d.on.godmode = (w.godmode == 1) or nil
    end)

    pcall(function()
        if w.nohunger == 1 or d.on.hunger then HungerLock(p, w.nohunger == 1, d) end
        d.on.hunger = (w.nohunger == 1) or nil
    end)

    -- movement: freeze holds them in place; speed (test) multiplies walking, running, sprinting
    -- and mount speeds. The speed stats are sent to their game, which is what their own
    -- movement reads; the server's walking speed follows so it doesn't pull them back.
    pcall(function()
        local m = p.CharacterMovement
        if not (d.move and d.move.walk and d.move.jump) then
            if m.MaxWalkSpeed < 1 or m.JumpZVelocity < 1 then return end
            d.move = { walk = m.MaxWalkSpeed, jump = m.JumpZVelocity }
            d.save("walk", m.MaxWalkSpeed)
            d.save("jump", m.JumpZVelocity)
        end
        local mult = tonumber(w.walk)
        if w.freeze == 1 then
            for _, name in ipairs(SPEEDS) do SetShared(ac, sidx, name, 0.0, d) end
            if m.MaxWalkSpeed ~= 0 then m.MaxWalkSpeed = 0 end
            if m.JumpZVelocity ~= 0 then m.JumpZVelocity = 0 end
        elseif mult then
            for _, name in ipairs(SPEEDS) do
                local key = "S_" .. name
                if d.attr[key] == nil then SetShared(ac, sidx, name, nil, d) end
                if d.attr[key] then SetShared(ac, sidx, name, d.attr[key] * mult, d) end
            end
            local want = d.move.walk * mult
            if math.abs(m.MaxWalkSpeed - want) > 0.5 then m.MaxWalkSpeed = want end
            if w.fly == 1 then m.MaxFlySpeed = 1500.0 * mult end
            if m.JumpZVelocity ~= d.move.jump then m.JumpZVelocity = d.move.jump end
        elseif d.on.freeze or d.on.walk then
            for _, name in ipairs(SPEEDS) do SetShared(ac, sidx, name, nil, d) end
            m.MaxWalkSpeed = d.move.walk
            m.JumpZVelocity = d.move.jump
        end
        d.on.freeze = (w.freeze == 1) or nil
        d.on.walk = (mult ~= nil and w.freeze ~= 1) or nil
    end)
end

local function ApplyPlayers()
    local all = ReadPWanted()
    for _, pl in ipairs(AllPlayers()) do
        if not pl.isLocal and pl.pawn and (all[pl.name] or PDefaults[pl.name]) then
            pcall(ApplyPlayer, pl, GodExpand(all[pl.name] or {}))
        end
    end
end

local function TeleportPawn(p, x, y, z)
    return p:K2_TeleportTo({ X = x, Y = y, Z = z }, p:K2_GetActorRotation())
end

-- Move another player, remembering where they were so "Send back" can undo it.
local function MoveFriend(pl, x, y, z)
    local here = pl.pawn:K2_GetActorLocation()
    PD(pl.name).back = { X = here.X, Y = here.Y, Z = here.Z }
    return TeleportPawn(pl.pawn, x, y, z)
end

-- Gravestones in the world, oldest first: { owner = hex id or nil, X, Y, Z }
local function Gravestones()
    local out = {}
    local reg = FindFirstOf("GameplayObjectRegistry")
    if not reg or not reg:IsValid() then return out end
    reg.Gravestones:ForEach(function(_, e)
        local g = e:get()
        local owner = nil
        pcall(function() owner = GuidHex(g.OwningCharGuid.InnerGuid) end)
        if not owner then pcall(function() owner = GuidHex(g.OwningCharGuid) end) end
        local ok, loc = pcall(function() return { X = g.Location.X, Y = g.Location.Y, Z = g.Location.Z } end)
        if ok then
            loc.owner = owner
            table.insert(out, loc)
        end
    end)
    return out
end

local function MyGuid()
    local f = io.open(DIR .. "me.txt", "r")
    if not f then return nil end
    local me = (f:read("*l") or ""):upper():gsub("[^0-9A-F]", "")
    f:close()
    return me ~= "" and me or nil
end

local function TryGuid(v)
    local hex = nil
    pcall(function() hex = GuidHex(v.InnerGuid) end)
    if not hex then pcall(function() hex = GuidHex(v) end) end
    if hex == "00000000000000000000000000000000" then hex = nil end
    return hex
end

-- A player's character id (the one gravestones are marked with). The game keeps it in a
-- property whose name has "Char" and "Guid" in it; look on the player state, controller,
-- character and their components. Remembered once found.
local function CharGuidOf(pl)
    local d = PD(pl.name)
    if d.guid then return d.guid, d.guidFrom end
    local objs = {}
    for _, o in ipairs({ pl.pc.PlayerState, pl.pc, pl.pawn }) do
        if o and o:IsValid() then
            table.insert(objs, o)
            for _, c in ipairs(Components(o)) do table.insert(objs, c) end
        end
    end
    for _, o in ipairs(objs) do
        local found, from = nil, nil
        pcall(function()
            local cls = o:GetClass()
            while cls and cls:IsValid() and not found do
                cls:ForEachProperty(function(prop)
                    if found then return end
                    local n = prop:GetFName():ToString()
                    local low = n:lower()
                    if low:find("guid") and (low:find("char") or low:find("persist")) then
                        local hex = TryGuid(o[n])
                        if hex then found, from = hex, o:GetClass():GetFName():ToString() .. "." .. n end
                    end
                end)
                cls = cls:GetSuperStruct()
            end
        end)
        if found then
            d.guid, d.guidFrom = found, from
            return found, from
        end
    end
    return nil
end

local function LatestGraveOf(pl)
    local graves = Gravestones()
    if #graves == 0 then return nil, "there are no gravestones in this world" end
    local guid = CharGuidOf(pl)
    local best = nil
    if guid then
        for _, g in ipairs(graves) do if g.owner == guid then best = g end end
        if not best then return nil, pl.name .. " has no gravestone" end
        return best
    end
    -- id unknown: with one friend online, their grave is the newest one that isn't yours
    local friends = 0
    for _, p in ipairs(AllPlayers()) do if not p.isLocal then friends = friends + 1 end end
    local me = MyGuid()
    if friends == 1 and me then
        for _, g in ipairs(graves) do if g.owner ~= me then best = g end end
        if best then return best end
        return nil, pl.name .. " has no gravestone"
    end
    return nil, "couldn't tell which gravestone is " .. pl.name .. "'s (send me inspect_player.txt from Player details)"
end

Actions["dw.p.grave"] = function(args)
    local pl = FindPlayer(args[1])
    local g, why = LatestGraveOf(pl)
    if not g then return why end
    MoveFriend(pl, g.X + 150.0, g.Y, g.Z + 200.0)
    return string.format("%s sent to their latest gravestone (%.0f, %.0f)", pl.name, g.X, g.Y)
end
Actions["dw.p.gotograve"] = function(args)
    local pl = FindPlayer(args[1])
    local g, why = LatestGraveOf(pl)
    if not g then return why end
    local _, me = Pawn()
    local here = me:K2_GetActorLocation()
    Back = { X = here.X, Y = here.Y, Z = here.Z }
    TeleportPawn(me, g.X + 150.0, g.Y, g.Z + 200.0)
    return string.format("teleported to %s's latest gravestone", pl.name)
end
Actions["dw.p.back"] = function(args)
    local pl = FindPlayer(args[1])
    local b = PD(pl.name).back
    if not b then return pl.name .. " hasn't been moved yet" end
    PD(pl.name).back = nil
    TeleportPawn(pl.pawn, b.X, b.Y, b.Z)
    return pl.name .. " sent back where they were"
end
-- Send a player to a map spot: x, y and (optional) ground height
Actions["dw.p.tp"] = function(args)
    local pl = FindPlayer(args[1])
    local x, y = tonumber(args[2]), tonumber(args[3])
    local z = tonumber(args[4]) or pl.pawn:K2_GetActorLocation().Z
    MoveFriend(pl, x, y, z + 200.0)
    return string.format("%s sent to (%.0f, %.0f)", pl.name, x, y)
end
-- Player details for troubleshooting: every value on their character, plus their id
Actions["dw.p.inspect"] = function(args)
    local pl = FindPlayer(args[1])
    local out = {}
    local guid, from = CharGuidOf(pl)
    table.insert(out, "charGuid\t" .. tostring(guid) .. "\t" .. tostring(from))
    for _, t in ipairs({ { "Controller", pl.pc }, { "Pawn", pl.pawn }, { "PlayerState", pl.pc.PlayerState } }) do
        if t[2] and t[2]:IsValid() then
            DumpProps(t[2], t[1], out)
            for _, c in ipairs(Components(t[2])) do DumpProps(c, t[1] .. "." .. c:GetFName():ToString(), out) end
        end
    end
    write("inspect_player.txt", table.concat(out, "\n") .. "\n")
    return string.format("%s: id %s (from %s); details in inspect_player.txt", pl.name, tostring(guid), tostring(from))
end
-- The same action for every other player: dw.all <action> [args...]
Actions["dw.all"] = function(args)
    local fn = Actions["dw.p." .. tostring(args[1])]
    if not fn then return "unknown action" end
    local done = {}
    for _, pl in ipairs(AllPlayers()) do
        if not pl.isLocal and pl.pawn then
            local call = { pl.name }
            for i = 2, #args do table.insert(call, args[i]) end
            local ok, msg = pcall(fn, call)
            table.insert(done, ok and tostring(msg) or (pl.name .. ": " .. tostring(msg)))
        end
    end
    if #done == 0 then return "no friends in your world" end
    return table.concat(done, "; ")
end

Actions["dw.p.heal"] = function(args)
    local pl = FindPlayer(args[1])
    Heal(pl.pawn)
    return pl.name .. ": health and stamina full"
end
Actions["dw.p.refill"] = function(args)
    local pl = FindPlayer(args[1])
    Refill(pl.pawn)
    return pl.name .. ": food, water and rest refilled"
end
Actions["dw.p.give"] = function(args)
    local pl = FindPlayer(args[1])
    local inv = pl.pc.BP_Components_Inventory
    local d = ItemData(args[2])
    local before = inv:GetNumItemsByData(d)
    local ok = inv:AddItemByData(d, tonumber(args[3]) or 1, 1.0, {})
    return string.format("%s: %s %d -> %d", pl.name, ok and "given" or "no room", before, inv:GetNumItemsByData(d))
end
Actions["dw.p.take"] = function(args)
    local pl = FindPlayer(args[1])
    local inv = pl.pc.BP_Components_Inventory
    local d = ItemData(args[2])
    local have = inv:GetNumItemsByData(d)
    local n = math.min(have, tonumber(args[3]) or have)
    if n > 0 then inv:RemoveItemByData(d, n) end
    return string.format("%s: %d -> %d", pl.name, have, inv:GetNumItemsByData(d))
end
Actions["dw.p.bring"] = function(args)
    local pl = FindPlayer(args[1])
    local _, me = Pawn()
    local l = me:K2_GetActorLocation()
    local ok = MoveFriend(pl, l.X + 150.0, l.Y, l.Z + 100.0)
    return ok and (pl.name .. " brought to you") or "teleport refused by the game"
end
Actions["dw.p.goto"] = function(args)
    local pl = FindPlayer(args[1])
    local _, me = Pawn()
    local here = me:K2_GetActorLocation()
    Back = { X = here.X, Y = here.Y, Z = here.Z }
    local l = pl.pawn:K2_GetActorLocation()
    TeleportPawn(me, l.X + 150.0, l.Y, l.Z + 100.0)
    return "teleported to " .. pl.name
end
Actions["dw.p.home"] = function(args)
    local x, y, z = ReadHome()
    if not x then return "no home set yet" end
    local pl = FindPlayer(args[1])
    MoveFriend(pl, x + 150.0, y, z + 50.0)
    return pl.name .. " sent to your home"
end
Actions["dw.p.revealmap"] = function(args)
    local pl = FindPlayer(args[1])
    local fog = pl.pc.RevealedMapFogComponent
    fog.RevealedMapRegionsBitmap = 4294967295
    return pl.name .. ": whole map revealed"
end
-- Put every value this mod ever changed on that player back to normal right now.
Actions["dw.p.normal"] = function(args)
    local pl = FindPlayer(args[1])
    local p, d = pl.pawn, PD(pl.name)
    local ac = p.PlayerEncumbranceComponent.AttributesComponent
    local idx, sidx = AttrIndex(ac), SharedIndex(ac)
    pcall(function() p.HealthComponent:SetCanDie(true) end)
    for k, _ in pairs(d.attr) do
        if k:sub(1, 2) == "S_" then
            if k ~= "S_HealthAttribute" then pcall(SetShared, ac, sidx, k:sub(3), nil, d) end
        else
            pcall(SetAttr, ac, idx, k, nil, d)
        end
    end
    pcall(function()
        local m = p.CharacterMovement
        if d.move then m.MaxWalkSpeed = d.move.walk; m.JumpZVelocity = d.move.jump end
        if d.on.fly then
            m.bCheatFlying = false
            m:SetMovementMode(3, 0)
            pcall(function() pl.pc:ClientCheatWalk() end)
            d.landUntil = os.clock() + 10
        end
    end)
    pcall(function()
        for _, c in ipairs(FindAllOf("ItemDurabilityComponent") or {}) do
            local o = c:GetOwner()
            if d.lossRate and o and o:IsValid() and o:GetAddress() == p:GetAddress() then c.DurabilityLossRate = d.lossRate end
        end
    end)
    pcall(NudgeHealth, p.HealthComponent)
    d.god, d.on = false, {}
    return pl.name .. ": everything back to normal"
end

-- ------------------------------------------------------------- numpad hotkeys
-- Toggles edit wanted.txt (so the editor's switches stay in step); actions run directly.
local function WriteWanted(w)
    local keys = {}
    for k in pairs(w) do table.insert(keys, k) end
    table.sort(keys)
    local lines = {}
    for _, k in ipairs(keys) do table.insert(lines, k .. "=" .. tostring(w[k])) end
    write("wanted.txt", table.concat(lines, "\n") .. (#lines > 0 and "\n" or ""))
end

local function Toggle(key, onValue)
    local w = ReadWanted()
    if w[key] ~= nil then w[key] = nil else w[key] = onValue end
    WriteWanted(w)
    ExecuteInGameThread(function() pcall(UpdateOverlay, w) end)
    log(key .. (w[key] and " on" or " off"))
    append("results.txt", math.floor(os.time() * 1000) .. "\tok\thotkey: " .. key .. (w[key] and " on" or " off"))
end

local function Do(name)
    ExecuteInGameThread(function()
        local ok, msg = pcall(Actions[name], {})
        append("results.txt", math.floor(os.time() * 1000) .. "\t" .. (ok and "ok" or "error") .. "\thotkey: " .. tostring(msg))
    end)
end

Actions["dw.normal"] = function()
    local keep = ReadWanted().hideoverlay
    WriteWanted({ hideoverlay = keep })
    return "all cheats back to normal"
end

-- Every game starts with all your cheats off (the top bar's show/hide choice is kept).
do
    local keep = ReadWanted().hideoverlay
    WriteWanted({ hideoverlay = keep })
end

local HOTKEYS = {
    { Key.NUM_ONE, function() Toggle("godmode", 1) end },
    { Key.NUM_TWO, function() Toggle("carry", 99999) end },
    { Key.NUM_THREE, function() Do("dw.refill") end },
    { Key.NUM_FOUR, function() Do("dw.heal") end },
    { Key.NUM_FIVE, function() Toggle("durability", 1) end },
    { Key.NUM_SIX, function() Toggle("nohunger", 1) end },
    { Key.NUM_SEVEN, function() Toggle("walk", 2) end },
    { Key.NUM_EIGHT, function() Toggle("damage", 10) end },
    { Key.NUM_NINE, function() Do("dw.grave") end },
    { Key.DECIMAL, function() Do("dw.return") end },
    { Key.NUM_ZERO, function() Do("dw.normal") end },
    { Key.ADD, function() Toggle("fly", 1) end },
    { Key.MULTIPLY, function() Toggle("hideoverlay", 1) end },
    { Key.SUBTRACT, function() Do("dw.home") end },
    { Key.DIVIDE, function() Do("dw.sethome") end },
}
for _, hk in ipairs(HOTKEYS) do
    pcall(function() RegisterKeyBind(hk[1], hk[2]) end)
end

local function Run(seq, fnName, args)
    if Actions[fnName] then
        local ok, msg = pcall(Actions[fnName], args)
        append("results.txt", seq .. "\t" .. (ok and "ok" or "error") .. "\t" .. tostring(msg):gsub("[\r\n\t]", " "))
        return
    end
    if fnName == "dw.inspect" or fnName == "dw.lua" then
        local ok, msg = pcall(function()
            if fnName == "dw.inspect" then return Inspect() end
            return DevLua(table.concat(args, "\t"))
        end)
        append("results.txt", seq .. "\t" .. (ok and "ok" or "error") .. "\t" .. tostring(msg):gsub("[\r\n\t]", " "):sub(1, 3000))
        return
    end
    local cm, why = GetCheatManager()
    if not cm then
        append("results.txt", seq .. "\terror\t" .. why)
        return
    end
    local types = ParamTypes(fnName)
    local call = {}
    for i, a in ipairs(args) do
        call[i] = Convert(a, types[i])
    end
    local ok, err = pcall(function()
        local fn = cm[fnName]
        if not fn or (fn.IsValid and not fn:IsValid()) then
            error("command not found: " .. fnName)
        end
        fn(cm, table.unpack(call))
    end)
    if ok then
        append("results.txt", seq .. "\tok\t" .. fnName)
    else
        append("results.txt", seq .. "\terror\t" .. tostring(err):gsub("[\r\n\t]", " "))
    end
end

local function ReadCommands()
    local f = io.open(DIR .. "commands.txt", "r")
    if not f then
        return {}
    end
    local out = {}
    for line in f:lines() do
        local parts = split(line, "\t")
        local seq = tonumber(parts[1])
        if seq and parts[2] and parts[2] ~= "" then
            local args = {}
            for i = 3, #parts do
                table.insert(args, parts[i])
            end
            table.insert(out, { seq = seq, fn = parts[2], args = args })
        end
    end
    f:close()
    return out
end

-- Ignore commands that were already in the file when the game started.
for _, c in ipairs(ReadCommands()) do
    if c.seq > lastSeq then
        lastSeq = c.seq
    end
end

local tick = 0
LoopAsync(500, function()
    tick = tick + 1
    local pending = {}
    for _, c in ipairs(ReadCommands()) do
        if c.seq > lastSeq then
            table.insert(pending, c)
        end
    end
    if #pending > 0 then
        table.sort(pending, function(a, b) return a.seq < b.seq end)
        lastSeq = pending[#pending].seq
        ExecuteInGameThread(function()
            for _, c in ipairs(pending) do
                Run(c.seq, c.fn, c.args)
            end
        end)
    end
    if tick % 2 == 0 then
        ExecuteInGameThread(function()
            pcall(ApplyWanted)
            pcall(ApplyPlayers)
        end)
    end
    if tick % 4 == 0 then
        ExecuteInGameThread(function()
            local pc = MyController()
            local player = pc and pc:IsValid()
            local cmReady = false
            if player then
                local cm = GetCheatManager()
                cmReady = cm ~= nil
                if not dumped then
                    DumpFunctions()
                end
            end
            write("status.txt", os.time() .. "\t" .. tostring(player) .. "\t" .. tostring(cmReady) .. "\t" .. MOD_VERSION .. "\n")
            pcall(WritePlayers)
        end)
    end
    return false -- keep looping
end)

-- Recreate the cheat menu whenever the player respawns or changes world.
RegisterHook("/Script/Engine.PlayerController:ClientRestart", function()
    ExecuteInGameThread(function()
        GetCheatManager()
    end)
end)

log("loaded")
